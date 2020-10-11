import numpy
import time
import math
import random
from collections import deque

class mppt:
  """
  Maximum power point tracker class
  """
  Voc = None
  Isc = None
  Vmpp = None  # voltage at max power point
  Impp = None  # current at max power point
  Pmax = None  # power at max power point (for keeping track of voc and isc)
  abort = False

  # under no circumstances should we violate this
  absolute_current_limit = 0.1  # always safe default
  
  currentCompliance = None
  t0 = None  # the time we started the mppt algorithm
  
  def __init__(self, sm, absolute_current_limit):
    self.sm = sm
    self.absolute_current_limit = absolute_current_limit
    
  def reset(self):
    self.Voc = None
    self.Isc = None
    self.Vmpp = None  # voltage at max power point
    self.Impp = None  # current at max power point
    self.Pmax = None
    self.abort = False
    
    self.current_compliance = None
    self.t0 = None  # the time we started the mppt algorithm
    
  def register_curve(self, vector, light=True):
    """
    registers an IV curve with the max power point tracker
    given a list of raw measurements, figures out which one produced the highest power
    updates some values for mppt if light=True
    """
    v = numpy.array([e[0] for e in vector])
    i = numpy.array([e[1] for e in vector])
    p = v*i*-1
    iscIndex = numpy.argmin(abs(v))
    Isc = i[iscIndex]
    vocIndex = numpy.argmin(abs(i))
    Voc = v[vocIndex]
    maxIndex = numpy.argmax(p)
    Vmpp = v[maxIndex]
    Pmax = p[maxIndex]
    Impp = i[maxIndex]
    if light is True:  # this was from a light i-v curve
      if (self.Pmax is None) or (Pmax > self.Pmax):
        self.Vmpp = Vmpp
        self.Impp = Impp
        self.Pmax = Pmax
        if min(v) <=0 and max(v)>=0:  # if we had data on both sizes of 0V, then we can estimate Isc
          self.Isc = Isc
          self.current_compliance = abs(Isc)*3
        if min(i) <=0 and max(i)>=0:  # if we had data on both sizes of 0A, then we can estimate Voc
          self.Voc = Voc
    # returns maximum power[W], Vmpp, Impp and the index
    return (Pmax, Vmpp, Impp, maxIndex)

  def launch_tracker(self, duration=30, callback=lambda x:None, NPLC=-1, compliance_override=None, extra="basic://7:10"):
    """
    general function to call begin a max power point tracking algorithm
    duration given in seconds, optionally calling callback function on each measurement point
    """
    m = []  # list holding mppt measurements
    self.t0 = time.time()  # start the mppt timer

    if self.current_compliance is None:
      current_compliance = 0.04  # assume 40mA compliance if nobody told us otherwise
    else:
      current_compliance = self.current_compliance
    
    if compliance_override is not None:
      current_compliance = compliance_override
      
    if NPLC != -1:
      self.sm.setNPLC(NPLC)
    
    if (self.Voc is None):
      print("Learning Voc...")
      # TODO: trouble here if Voc is larger than 3V...
      self.sm.setupDC(sourceVoltage=False, compliance=3, setPoint=0, senseRange='a')
      self.sm.write(':arm:source immediate') # this sets up the trigger/reading method we'll use below
      ssvocs=self.sm.measureUntil(t_dwell=1)
      self.Voc = ssvocs[-1][0]
    else:
      ssvocs = []

    if self.Vmpp is None:
      self.Vmpp = 0.7 * self.Voc # start at 70% of Voc if nobody told us otherwise

    # clamp current to global limit
    if current_compliance > self.absolute_current_limit:
      current_compliance = self.absolute_current_limit

    self.sm.setupDC(sourceVoltage=True, compliance=current_compliance, setPoint=self.Vmpp, senseRange='f')
    self.sm.write(':arm:source immediate')  # this sets up the trigger/reading method we'll use below

    # if no compliance override, do our own compliance set via one single measurement here
    if compliance_override is None:
      m.append(cm:=self.sm.measure()[0])
      callback(cm)
      current_compliance = abs(cm[1] * 2)  # current compliance is 2x impp
      # clamp current to global limit
      if current_compliance > self.absolute_current_limit:
        current_compliance = self.absolute_current_limit
      self.sm.setupDC(sourceVoltage=True, compliance=current_compliance, setPoint=self.Vmpp, senseRange='f')
      self.current_compliance = current_compliance

    if self.Voc >= 0:
      self.voltage_lock = True  # lock mppt voltage to be >0
    else:
      self.voltage_lock = False  # lock mppt voltage to be <0
    # run a tracking algorithm
    extra_split = extra.split(sep='://', maxsplit=1)
    algo = extra_split[0]
    params = extra_split[1]
    if algo == 'basic':
      if len(params) == 0: #  use defaults
        m.append(m_tracked:=self.really_dumb_tracker(duration, callback=callback))
      else:
        params = params.split(':')
        if len(params) != 2:
          raise (ValueError("MPPT configuration failure, Usage: --mppt-params basic://[degrees]:[dwell]"))
        params = [float(f) for f in params]
        m.append(m_tracked:=self.really_dumb_tracker(duration, callback=callback, dAngleMax=params[0], dwell_time=params[1]))
    elif (algo == 'gd'):
      if len(params) == 0:  #  use defaults
        m.append(m_tracked:=self.gradient_descent(duration, start_voltage=self.Vmpp, alpha=1, min_step=0.0001, NPLC=10, callback=callback))
      else:
        params = params.split(':')
        if len(params) != 3:
          raise (ValueError("MPPT configuration failure, Usage: --mppt-params gd://[alpha]:[min_step]:[NPLC]"))        
        params = [float(f) for f in params]
        m.append(m_tracked:=self.gradient_descent(duration, start_voltage=self.Vmpp, callback=callback, alpha=params[0], min_step=params[1], NPLC=params[2]))
    else:
      print('WARNING: MPPT algorithm {:} not understood, not doing max power point tracking'.format(algo))

    run_time = time.time() - self.t0
    print('Final value seen by the max power point tracker after running for {:.1f} seconds is'.format(run_time))
    print('{:0.4f} mW @ {:0.2f} mV and {:0.2f} mA'.format(self.Vmpp*self.Impp*1000*-1, self.Vmpp*1000, self.Impp*1000))
    return (m, ssvocs)

  def gradient_descent(self, duration, start_voltage, callback=lambda x:None, alpha=1, min_step=0, NPLC=-1):
    """
    gradient descent MPPT algorithm
    alpha is the "learning rate"
    min_step is the minimum voltage step size the algorithm will be allowed to take
    fade_in_t is the number of seconds to use to ramp the learning rate from 0 to alpha at the start of the algorithm
    """
    max_step = 0.1
    if NPLC != -1:
      self.sm.setNPLC(NPLC)
    print("===Starting up gradient descent maximum power point tracking algorithm===")
    print(f"Learning rate (alpha) = {alpha}")
    print(f"Smallest step (min_step) = {min_step*1000} [mV]")
    print(f"Largest step (max_step) = {max_step*1000} [mV]")
    print(f"NPLC = {self.sm.sm.query(':sense:current:nplcycles?')}")

    self.q = deque()
    m = deque(maxlen=2) # keeps two measurements
    x = deque(maxlen=2) # keeps two x values
    y = deque(maxlen=2) # keeps two loss(y) values

    # the loss function we'll be minimizing here is power produced by the sourcemeter
    loss = lambda a, b, t: a * b * -1

    # do one bootstrap measurement
    W = start_voltage
    m.append(self.measure(W, callback=callback))
    x.append(W)
    y.append(loss(*m[-1]))
    run_time = m[-1][2] - self.t0 # recompute runtime

    # get the sign of a number
    sign = lambda x: (1, -1)[int(x<0)]

    big = float("Infinity")
    while (not self.abort and (run_time < duration)):
      m.append(self.measure(W, callback=callback))  # apply new voltage and record a measurement
      x.append(W)
      y.append(loss(*m[-1]))  # calculate the new loss and save it
      run_time = time.time() - self.t0 # recompute runtime
      if x[-1] != x[-2]: # prevent div by zero
        gradient = (y[-1] - y[-2]) / (x[-1] - x[-2]) # calculate the slope in the loss function from the last two measurements
        v_step = alpha * gradient  # calculate the voltage step size based on alpha and the gradient
      else:  # handle div by zero
        some_sign = random.choice([-1,1])
        if min_step == 0:
          v_step = 0.0001
        else:
          v_step = min_step
        v_step = some_sign*v_step
      #print(f"rt={run_time}, a={alpha}, g={gradient}, step={v_step}")  # for debugging
      if (abs(v_step) < min_step) and (min_step > 0):  # enforce minimum step size if we're doing that
        v_step = sign(v_step) * min_step
      elif (abs(v_step) > max_step) and (max_step < big):  # enforce maximum step size if we're doing that
         v_step = sign(v_step) * max_step
      W += v_step # apply voltage step, calculate new voltage
      
    self.Impp = m[-1][1]
    self.Vmpp = m[-1][0]
    q = self.q
    del(self.q)
    return q
  
  def measure(self, v_set, callback=lambda x:None):
    """
    sets the voltage and makes a measurement
    #sets self.abort = true and shuts off the sourcemeter output
    #if the mppt wanders out of the power quadrant
    #this should protect the system from events like sudden open circuit or loss of light
    #causing the mppt to go haywire and asking the sourcemeter for dangerously high or low voltages
    """
    if (self.voltage_lock == True) and (v_set < 0):
      v_set = 0.0001
    elif (self.voltage_lock == False) and (v_set > 0):
      v_set = -0.0001

    self.sm.setSource(v_set)
    measurement = self.sm.measure()[0]
    callback(measurement)
    v, i, tx, status = measurement
    #print(f"s={int(status):b}")
    # if v * i > 0:  # TODO: test this
    #  self.abort = True
    #  self.sm.outOn(False)
    #  print("WARNING: Stopping max power point tracking because the MPPT algorithm wandered out of the power quadrant")
    self.q.append(measurement)
    return (v, i, tx)

  def really_dumb_tracker(self, duration, callback=lambda x:None, dAngleMax = 7, dwell_time = 10):
    """
    A super dumb maximum power point tracking algorithm that
    alternates between periods of exploration around the mppt and periods of constant voltage dwells
    runs for duration seconds and returns a nx4 deque of the measurements it made
    dAngleMax, exploration limits, [exploration degrees] (plus and minus)
    dwell_time, dwell period duration in seconds
    """
    print("===Starting up dumb maximum power point tracking algorithm===")
    print("dAngleMax = {:}[degrees]\ndwell_time = {:}[s]".format(dAngleMax, dwell_time))

    # work in voltage steps that are this fraction of Voc
    dV = self.Voc / 301
    
    self.q = deque()
    Vmpp = self.Vmpp

    if duration <= 10:
      # if the user only wants to mppt for 20 or less seconds, shorten the initial dwell
      initial_soak = duration * 0.2
    else:
      initial_soak = dwell_time

    print("Soaking @ Mpp (V={:0.2f}[mV]) for {:0.1f} seconds...".format(self.Vmpp*1000, initial_soak))
    ssmpps=self.sm.measureUntil(t_dwell=initial_soak, cb=callback)
    self.Impp = ssmpps[-1][1]  # use most recent current measurement as Impp
    if self.Isc is None:
      # if nobody told us otherwise, just assume Isc is 10% higher than Impp
      self.Isc = self.Impp * 1.1
    self.q.extend(ssmpps)
    
    Impp = self.Impp
    Voc = self.Voc
    Isc = self.Isc

    run_time = time.time() - self.t0
    while (not self.abort and (run_time < duration)):
      print("Exploring for new Mpp...")
      i_explore = numpy.array(Impp)
      v_explore = numpy.array(Vmpp)

      angleMpp = numpy.rad2deg(numpy.arctan(Impp/Vmpp*Voc/Isc))
      print('MPP ANGLE = {:0.2f}'.format(angleMpp))
      v_set = Vmpp
      highEdgeTouched = False
      lowEdgeTouched = False
      while (not self.abort and not(highEdgeTouched and lowEdgeTouched)):
        (v, i, t) = self.measure(v_set, callback=callback)
        run_time = t - self.t0

        i_explore = numpy.append(i_explore, i)
        v_explore = numpy.append(v_explore, v)
        thisAngle = numpy.rad2deg(numpy.arctan(i/v*Voc/Isc))
        dAngle = angleMpp - thisAngle
        # print("dAngle={:}, highEdgeTouched={:}, lowEdgeTouched={:}".format(dAngle, highEdgeTouched, lowEdgeTouched))
        
        if (highEdgeTouched == False) and (dAngle > dAngleMax):
          highEdgeTouched = True
          dV = dV * -1
          print("Reached high voltage edge because angle exceeded")
        
        if (lowEdgeTouched == False) and (dAngle < -dAngleMax):
          lowEdgeTouched = True
          dV = dV * -1
          print("Reached low voltage edge because angle exceeded")
          
        v_set = v_set + dV
        if ((v_set > 0) and (dV > 0)) or ((v_set < 0) and (dV < 0)):  #  walking towards Voc
          if (highEdgeTouched == False) and (dV > 0) and v_set >= Voc:
            highEdgeTouched = True
            dV = dV * -1 # switch our voltage walking direction
            v_set = v_set + dV
            print("WARNING: Reached high voltage edge because we hit Voc")
            
          if (lowEdgeTouched == False) and (dV < 0) and v_set <= Voc:
            lowEdgeTouched = True
            dV = dV * -1 # switch our voltage walking direction
            v_set = v_set + dV
            print("WARNING: Reached high voltage edge because we hit Voc")
            
          
        else: #  walking towards Jsc
          if (highEdgeTouched == False) and (dV > 0) and v_set >= 0:
            highEdgeTouched = True
            dV = dV * -1 # switch our voltage walking direction
            v_set = v_set + dV
            print("WARNING: Reached low voltage edge because we hit 0V")
            
          if (lowEdgeTouched == False) and (dV < 0) and v_set <= 0:
            lowEdgeTouched = True
            dV = dV * -1 # switch our voltage walking direction
            v_set = v_set + dV
            print("WARNING: Reached low voltage edge because we hit 0V")
        

      print("Done exploring.")

      # find the powers for the values we just explored
      p_explore = v_explore * i_explore * -1
      maxIndex = numpy.argmax(p_explore)
      Vmpp = v_explore[maxIndex]
      Impp = i_explore[maxIndex]

      print("New Mpp found: {:.6f} mW @ {:.6f} V".format(p_explore[maxIndex]*1000, Vmpp))

      dFromLastMppAngle = angleMpp - numpy.rad2deg(numpy.arctan(Impp/Vmpp*Voc/Isc))

      print("That's {:.6f} degrees different from the previous Mpp.".format(dFromLastMppAngle))
      
      
      #time_left = duration - run_time
      
      #if time_left <= 0:
      #  break
      
      print("Teleporting to Mpp!")
      self.sm.setSource(Vmpp)
      
      #if time_left < dwell_time:
      #  dwell = time_left
      #else:
      dwell = dwell_time
        
      print("Dwelling @ Mpp (V={:0.2f}[mV]) for {:0.1f} seconds...".format(Vmpp*1000, dwell))
      dq = self.sm.measureUntil(t_dwell=dwell, cb=callback)
      Impp = dq[-1][1]
      self.q.extend(dq)

      run_time = time.time() - self.t0
    
    q = self.q
    del(self.q)
    self.Impp = Impp
    self.Vmpp = Vmpp
    return q
