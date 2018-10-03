import mpmath
import time
import numpy
from collections import deque

class wavelabs():
  def startServer(self):
    pass
  def awaitConnection(self):
    print ("Connected to virtual lightsource")
  def activateRecipe(self, recipe):
    print ("Light engine recipe '{:}' virtually activated.".format(recipe))
  def startRecipe(self):
    print("Light engine recipe virtually started")
  def cancelRecipe(self):
    print("Light engine recipe virtually cancelled")
  def close(self):
    pass  

class pcb():
  def pix_picker(self, substrate, pixel, suppressWarning=False):
    return True

class k2400():
  """Solar cell device simulator (looks like k2400 class)
  """

  def __init__(self):
    self.t0 = time.time()
    self.measurementTime = 0.01  # [s] the time it takes the simulated sourcemeter to make a measurement

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

  def setNPLC(self,nplc):
    return

  def setupDC(self, sourceVoltage=True, compliance=0.1, setPoint=1):
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

  def setupSweep(self, sourceVoltage=True, compliance=0.1, nPoints=101, stepDelay=-1, start=0, end=1, streaming=False):
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

  def setOutput(self, outVal):
    self.write(':source:{:s} {:.6f}'.format(self.src,outVal))    

  def outOn(self, on=True):
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
        sweepArray = numpy.array([], dtype=numpy.float_).reshape(0, 4)
        voltages = numpy.linspace(self.sweepStart, self.sweepEnd, self.nPoints)
        for i in range(len(voltages)):
          self.V = voltages[i]
          self.updateCurrent()
          time.sleep(self.measurementTime)
          measurementLine = numpy.array([self.V, self.I, time.time()-self.t0, self.status])
          sweepArray = numpy.vstack([sweepArray, measurementLine])
        return sweepArray
      else:  # non sweep mode
        time.sleep(self.measurementTime)
        measurementLine = numpy.array([self.V, self.I, time.time()-self.t0, self.status])
        return measurementLine
    elif command == ":source:voltage:step?":
      dV = (self.sweepEnd - self.sweepStart)/self.nPoints
      return numpy.array([dV])
    else:
      raise ValueError("What?")

  def measureUntil(self, t_dwell=numpy.inf, measurements=numpy.inf, cb=lambda x:None):
    """Meakes measurements until termination conditions are met
    supports a callback after every measurement
    returns a queqe of measurements
    """
    i = 0
    t_end = time.time() + t_dwell
    q = deque()
    while (i < measurements) and (time.time() < t_end):
      i = i + 1
      measurement = self.measure()
      q.append(measurement)
      cb(measurement)
    return q    

  def measure(self):
    return self.query_values("READ?")

  def close(self):
    pass
