import mpmath
import time
import numpy
from collections import deque

class motion():
  def __init__(self, *args, **kwargs):
    print(f"virtual motion init args={args}, kwargs={kwargs}")
    addr = kwargs['address']
    content = addr.lstrip('us://')
    pieces = content.split('/')
    expected_lengths_in_mm = pieces[0]
    self.naxis = len(expected_lengths_in_mm.split(','))
  def connect(self):
    print ("Connected to virtual motion controller")
    return 0
  def move(self, mm):
    print("Virtually moving {:}mm".format(mm))
    return 0
  def goto(self, mm):
    print("Virtually moving to {:}mm".format(mm))
    return 0
  def home(self):
    print("Virtually homing")
    return 0
  def estop(self):
    return 0
  def get_position(self):
    return [14.1]*self.naxis

class illumination():
  runtime = 60
  def connect(self):
    print ("Connected to virtual lightsource")
    return(0)
  def activateRecipe(self, recipe):
    print ("Light engine recipe '{:}' virtually activated.".format(recipe))
    return(0)
  def on(self):
    print("Virtual light turned on")
    return(0)
  def off(self):
    print("Virtual light turned off")
    return(0)
  def get_spectrum(self):
    print("Virtual light turned off")
    print("Giving you a virtual spectrum")
    return ([],[])  # TODO: make this not empty
  def disconnect(self, *args, **kwargs):
    pass  
  def set_runtime(self, ms):
    self.runtime=ms
  def get_runtime(self):
    return(self.runtime)
def get_temperatures(self, *args, **kwargs):
  return([25.3,17.3])

class pcb():
  def __init__(self, *args, **kwargs):
    pass
  def pix_picker(self, *args, **kwargs):
    win = True
    return win
  def get(self, *args, **kwargs):
    return ""
  def __enter__(self):
    return(self)
  def __exit__(self, *args, **kwargs):
    pass

class k2400():
  """Solar cell device simulator (looks like k2400 class)
  """

  def __init__(self, *args, **kwargs):
    idn = 'Virtual Sourcemeter'
    self.t0 = time.time()
    self.measurementTime = 0.01  # [s] the time it takes the simulated sourcemeter to make a measurement

    # here we make up some numbers for our solar cell model
    self.Rs = 9.28  # [ohm]
    self.Rsh = 1e6  # [ohm]
    self.n = 3.58
    self.I0 = 260.4e-9  # [A]
    self.Iph = 6.293e-3  # [A]
    self.cellTemp = 29  # degC
    self.T = 273.15 + self.cellTemp  # cell temp in K
    self.K = 1.3806488e-23  # boltzman constant
    self.q = 1.60217657e-19  # electron charge
    self.Vth = mpmath.mpf(self.K*self.T/self.q)  # thermal voltage ~26mv
    self.V = 0  # voltage across device
    self.I = 0  # current through device
    self.updateCurrent()

    # for sweeps:
    self.sweepMode = False
    self.nPoints = 1001
    self.sweepStart = 1
    self.sweepEnd = 0

    self.status = 0
    self.four88point1 = True
    self.auto_ohms = False

  def __del__(self, *args, **kwargs):
    return

  def setNPLC(self, *args, **kwargs):
    return

  def disconnect(self, *args, **kwargs):
    return

  def setWires(self, *args, **kwargs):
    return

  def setTerminals(self, *args, **kwargs):
    return

  def updateSweepStart(self, startVal):
    self.sweepStart = startVal

  def updateSweepStop(self, stopVal):
    self.sweepEnd = stopVal

  def setupDC(self, sourceVoltage=True, compliance=0.04, setPoint=0, senseRange='f', auto_ohms = False):
    if auto_ohms == True:
      self.auto_ohms = True
    else:
      self.auto_ohms = False
      if sourceVoltage:
        src = 'voltage'
        snc = 'current'
      else:
        src = 'current'
        snc = 'voltage'
      self.src = src
      self.write(':source:{:s} {:0.6f}'.format(self.src,setPoint))
      self.sweepMode = False
    return

  def setupSweep(self, sourceVoltage=True, compliance=0.04, nPoints=101, stepDelay=-1, start=0, end=1, senseRange='f'):
    """setup for a sweep operation
    """
    #sm = self.sm
    if sourceVoltage:
      src = 'voltage'
      snc = 'current'
    else:
      src = 'current'
      snc = 'voltage'
    self.src = src
    self.nPoints = nPoints
    self.sweepMode = True
    self.sweepStart = start
    self.sweepEnd = end
    self.dV = abs(float(self.query_values(':source:voltage:step?')))

  def setSource(self, outVal):
    self.write(':source:{:s} {:.6f}'.format(self.src,outVal))    

  def outOn(self, on=True):
    return

  def opc(self, *args, **kwargs):
    return

  # the device is open circuit
  def openCircuitEvent(self):
    self.I = 0
    Rs = self.Rs
    Rsh = self.Rsh
    n = self.n
    I0 = self.I0
    Iph = self.Iph
    Vth = self.Vth
    Voc = I0*Rsh + Iph*Rsh - Vth*n*mpmath.lambertw(I0*Rsh*mpmath.exp(Rsh*(I0 + Iph)/(Vth*n))/(Vth*n))
    self.V = float(numpy.real_if_close(numpy.complex(Voc)))

  # recompute device current
  def updateCurrent(self):
    Rs = self.Rs
    Rsh = self.Rsh
    n = self.n
    I0 = self.I0
    Iph = self.Iph
    Vth = self.Vth
    V = self.V
    I = (Rs*(I0*Rsh + Iph*Rsh - V) - Vth*n*(Rs + Rsh)*mpmath.lambertw(I0*Rs*Rsh*mpmath.exp((Rs*(I0*Rsh + Iph*Rsh - V)/(Rs + Rsh) + V)/(Vth*n))/(Vth*n*(Rs + Rsh))))/(Rs*(Rs + Rsh))
    self.I = float(-1*numpy.real_if_close(numpy.complex(I)))

  def write(self, command):
    if ":source:current " in command:
      currentVal = float(command.split(' ')[1])
      if currentVal == 0:
        self.openCircuitEvent()
      else:
        raise ValueError("Can't do currents other than zero right now!")
    elif command == ":source:voltage:mode sweep":
      self.sweepMode = True
    elif command == ":source:voltage:mode fixed":
      self.sweepMode = False
    elif ":source:sweep:points " in command:
      self.nPoints = int(command.split(' ')[1])
    elif ":source:voltage:start " in command:
      self.sweepStart = float(command.split(' ')[1])
    elif ":source:voltage:stop " in command:
      self.sweepEnd = float(command.split(' ')[1])
    elif ":source:voltage " in command:
      self.V = float(command.split(' ')[1])
      self.updateCurrent()

  def query_ascii_values(self, command):
    return self.query_values(command)

  def read(self):
    return(self.query_values("READ?"))

  def query_values(self, command):
    if command == "READ?":
      if self.sweepMode:
        sweepArray = []
        voltages = numpy.linspace(self.sweepStart, self.sweepEnd, self.nPoints)
        for i in range(len(voltages)):
          self.V = voltages[i]
          self.updateCurrent()
          time.sleep(self.measurementTime)
          if self.auto_ohms == False:
            measurementLine = list([self.V, self.I, time.time()-self.t0, self.status])
          else:
            measurementLine = list([self.V, self.I, self.V/self.I, time.time()-self.t0, self.status])
          sweepArray.append(measurementLine)
        self.last_sweep_time = sweepArray[-1][2] - sweepArray[0][2]
        print(f"Sweep duration = {self.last_sweep_time} s")
        return sweepArray
      else:  # non sweep mode
        time.sleep(self.measurementTime)
        if self.auto_ohms == False:
          measurementLine = list([self.V, self.I, time.time()-self.t0, self.status])
        else:
          measurementLine = list([self.V, self.I, self.V/self.I, time.time()-self.t0, self.status])
        return [measurementLine]
    elif command == ":source:voltage:step?":
      dV = (self.sweepEnd - self.sweepStart)/self.nPoints
      return dV
    elif command == ":source:current:step?":
      dI = (self.sweepEnd - self.sweepStart)/self.nPoints
      return dI
    else:
      raise ValueError("What?")

  def measureUntil(self, t_dwell=float('Infinity'), measurements=float('Infinity'), cb=lambda x:None):
    """Meakes measurements until termination conditions are met
    supports a callback after every measurement
    returns a queqe of measurements
    """
    i = 0
    t_end = time.time() + t_dwell
    q = []
    while (i < measurements) and (time.time() < t_end):
      i = i + 1
      measurement = self.measure()[0]
      q.append(measurement)
      cb(measurement)
    return q    

  def measure(self, nPoints=1):
    if self.auto_ohms == False:
      m_len = 4
    else:
      m_len = 5
    return self.query_values("READ?")

  def contact_check(self, *args, **kwargs):
    return True

  def close(self):
    pass
