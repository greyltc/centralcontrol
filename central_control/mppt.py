import numpy
import time
from collections import deque

class mppt:
  """
  Maximum power point tracker class
  """
  Voc = None
  Isc = None
  Vmpp = None  # voltage at max power point
  Impp = None  # current at max power point
  
  currentCompliance = None
  t0 = None  # the time we started the mppt algorithm
  
  def __init__(self, sm):
    self.sm = sm
    
  def reset(self):
    Voc = None
    Isc = None
    Vmpp = None  # voltage at max power point
    Impp = None  # current at max power point
    
    current_compliance = None
    t0 = None  # the time we started the mppt algorithm
    
  def which_max_power(self, vector):
    """
    given a list of raw measurements, figure out which one produced the highest power
    """
    v = numpy.array([e[0] for e in vector])
    i = numpy.array([e[1] for e in vector])  
    p = v*i*-1
    maxIndex = numpy.argmax(p)
    Vmpp = v[maxIndex]
    Pmax = p[maxIndex]
    Impp = i[maxIndex]
    # returns maximum power[W], Vmpp, Impp and the index
    return (Pmax, Vmpp, Impp, maxIndex)
    
  def launch_tracker(self, duration=30, callback = None, NPLC=-1, extra="basic://7:10"):
    """
    general function to call begin a max power point tracking algorithm
    duration given in seconds, optionally calling callback function on each measurement point
    """
    if (self.Voc == None):
      print("WARNING: Not doing power point tracking. Voc not known.")
      return []
    self.t0 = time.time()  # start the mppt timer

    if self.Vmpp == None:
      self.Vmpp = 0.7 * self.Voc # start at 70% of Voc if nobody told us otherwise
      
    if self.current_compliance == None:
      current_compliance = 0.04  # assume 40mA compliance if nobody told us otherwise
    else:
      current_compliance = self.current_compliance
      
    if NPLC != -1:
      self.sm.setNPLC(NPLC)
    
    # do initial mppt dwell before we start the actual algorithm
    print("Teleporting to Mpp!")
    self.sm.setupDC(sourceVoltage=True, compliance=current_compliance, setPoint=self.Vmpp, senseRange='a')
    self.sm.write(':arm:source immediate') # this sets up the trigger/reading method we'll use below
    if duration <= 10:
      # if the user only wants to mppt for 20 or less seconds, shorten the initial dwell
      initial_soak = duration * 0.2
    else:
      initial_soak = 10
    print("Soaking @ Mpp (V={:0.2f}[mV]) for {:0.1f} seconds...".format(self.Vmpp*1000, initial_soak))
    q = self.sm.measureUntil(t_dwell=initial_soak)
    self.Impp = q[-1][1]  # use most recent current measurement as Impp
    if self.current_compliance == None:
      self.current_compliance = abs(self.Impp * 2)
    if self.Isc == None:
      # if nobody told us otherwise, assume Isc is 10% higher than Impp
      self.Isc = self.Impp * 1.1
  
    # run a tracking algorithm
    extra_split = extra.split(sep='://', maxsplit=1)
    algo = extra_split[0]
    params = extra_split[1]
    pptv = []
    if algo == 'basic':
      if len(params) == 0: #  use defaults
        pptv = self.really_dumb_tracker(duration, callback)
      else:
        params = params.split(':')
        if len(params) != 2:
          raise (ValueError("MPPT configuration failure, Usage: --mppt-params basic://[degrees]:[dwell]"))
        params = [float(f) for f in params]
        pptv = self.really_dumb_tracker(duration, callback, dAngleMax=params[0], dwell_time=params[1])
    elif (algo == 'gradient_descent'):
      if len(params) == 0: #  use defaults
        pptv = self.gradient_descent(duration, callback)
      else:
        params = params.split(':')
        if len(params) != 3:
          raise (ValueError("MPPT configuration failure, Usage: --mppt-params gradient_descent://[alpha]:[min_step]:[fade_in]"))        
        params = [float(f) for f in params]
        pptv = self.gradient_descent(duration, callback, alpha=params[0], min_step=params[1], fade_in=params[2])
    else:
      print('WARNING: MPPT algorithm {:} not understood, not doing max power point tracking'.format(algo))
    
    q.extend(pptv)
    run_time = time.time() - self.t0
    print('Final value seen by the max power point tracker after running for {:.1f} seconds is'.format(run_time))
    print('{:0.4f} mW @ {:0.2f} mV and {:0.2f} mA'.format(self.Vmpp*self.Impp*1000*-1, self.Vmpp*1000, self.Impp*1000))    
    return q
  
  def gradient_descent(self, duration, callback = None, alpha = 10, min_step = 0.001, fade_in = 10):
    """
    gradient descent MPPT algorithm
    alpha is the "learning rate"
    min_step is the minimum voltage step size the algorithm will be allowed to take
    fade_in is the number of seconds to use to ramp the learning rate from 0 to alpha at the start of the algorithm
    """
    print("===Starting up gradient descent maximum power point tracking algorithm===")
    print("Learning rate (alpha) = {:}".format(alpha))
    print("Smallest step (min_step) = {:} [mV]".format(min_step*1000))
    print("Ramp up time (fade_in) = {:} [s]".format(fade_in))
    
    # initial voltage step size
    # dV = self.Voc / 1001
    
    self.q = deque()

    W = self.Vmpp
    last = (self.Vmpp, self.Impp)
    
    # the loss function we'll use here is just power * -1 so that minimzing loss maximizes power
    loss = lambda x, y: -1 * x * y
    
    # get the sign of a number
    sign = lambda x: (1, -1)[int(x<0)]
    
    given_alpha = alpha
    run_time = time.time() - self.t0
    abort = False
    while (not abort and (run_time < duration)):
      # slowly ramp up alpha
      if run_time < fade_in:
        alpha = run_time/fade_in * given_alpha
      else:
        alpha = given_alpha
      
      # apply new voltage and record a measurement
      v, i, abort = self.measure(W)
      this = (v, i)
      if this[0] == last[0]: # do nothing if two consecutive voltages are the same (prevents div by zer below)
        pass
        #W += 1e-6 # bump the voltage by a microvolt if we couldn't sense a voltage change (prevents div by zer below)
      else:
        gradient = (loss(*this) - loss(*last)) / (this[0] - last[0]) # calculate the slope in the loss function
        v_step = alpha * gradient # calculate the voltage step size based on alpha and the gradient
        if (abs(v_step) < min_step) and (min_step > 0): # enforce minimum step size if we're doing that
          v_step = sign(v_step) * min_step
        W += v_step # apply voltage step
      last = this #  save the measuerment we just took for comparison in the next loop iteration
      run_time = time.time() - self.t0 # recompute runtime
    self.Impp = i
    self.Vmpp = v
    q = self.q
    del(self.q)
    return q
  
  def measure(self, v_set):
    """
    sets the voltage and makes a measurement
    #returns abort = true and shuts off the sourcemeter output
    #if the mppt wanders out of the power quadrant
    #this should protect the system from events like sudden open circuit or loss of light
    #causing the mppt to go haywire and asking the sourcemeter for dangerously high or low voltages
    """
    self.sm.setSource(v_set)
    measurement = self.sm.measure()
    [v, i, tx, status] = measurement
    abort = False
    # if v * i > 0:
    #  abort = True
    #  self.sm.outOn(False)
    #  print("WARNING: Stopping max power point tracking because the MPPT algorithm wandered out of the power quadrant")
    self.q.append(measurement)
    return v, i, abort

  def really_dumb_tracker(self, duration, callback = None, dAngleMax = 7, dwell_time = 10):
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
    
    Impp = self.Impp
    Vmpp = self.Vmpp
    Voc = self.Voc
    Isc = self.Isc
    
    abort = False
    run_time = time.time() - self.t0
    while (not abort and (run_time < duration)):
      print("Exploring for new Mpp...")
      i_explore = numpy.array(Impp)
      v_explore = numpy.array(Vmpp)

      angleMpp = numpy.rad2deg(numpy.arctan(Impp/Vmpp*Voc/Isc))
      print('MPP ANGLE = {:0.2f}'.format(angleMpp))
      v_set = Vmpp
      highEdgeTouched = False
      lowEdgeTouched = False
      while (not abort and not(highEdgeTouched and lowEdgeTouched)):
        v, i, abort = self.measure(v_set)

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
      
      run_time = time.time() - self.t0
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
      if callback != None:
        dq = self.sm.measureUntil(t_dwell=dwell, cb=callback)
      else:
        dq = self.sm.measureUntil(t_dwell=dwell)
      Impp = dq[-1][1]
      self.q.extend(dq)

      run_time = time.time() - self.t0
    
    q = self.q
    del(self.q)
    self.Impp = Impp
    self.Vmpp = Vmpp
    return q
