class devsim():
  """Solar cell device simulator
  """
  readyForAction = True
  
  def __init__(self):
    myPrint("Dummy mode initiated...", file=sys.stderr, flush=True)
    self.t0 = time.time()
    self.measurementTime = 0.01 # [s] the time it takes the simulated sourcemeter to make a measurement

    self.Rs = 9.28 #[ohm]
    self.Rsh = 1e6 #[ohm]
    self.n = 3.58
    self.I0 = 260.4e-9#[A]
    self.Iph = 6.293e-3#[A]
    self.cellTemp = 29 #degC
    self.T = 273.15 + self.cellTemp #cell temp in K
    self.K = 1.3806488e-23 #boltzman constant
    self.q = 1.60217657e-19 #electron charge
    self.Vth = mpmath.mpf(self.K*self.T/self.q) #thermal voltage ~26mv
    self.V = 0 # voltage across device
    self.I = None# current through device
    self.updateCurrent()

    # for sweeps:
    self.sweepMode = False
    self.nPoints = 1001
    self.sweepStart = 1
    self.sweepEnd = 0

    self.status = 0

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
    self.I = float(numpy.real_if_close(numpy.complex(I)))

  def write (self, command):
    if command == ":source:current 0":
      self.openCircuitEvent()
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

  def query_values(self, command):
    if command == "READ?":
      if self.sweepMode:
        sweepArray = numpy.array([],dtype=numpy.float_).reshape(0,4)
        voltages = numpy.linspace(self.sweepStart,self.sweepEnd,self.nPoints)
        for i in range(len(voltages)):
          self.V = voltages[i]
          self.updateCurrent()
          time.sleep(self.measurementTime)
          measurementLine = numpy.array([self.V, self.I, time.time()-self.t0, self.status])
          sweepArray = numpy.vstack([sweepArray,measurementLine])
        return sweepArray
      else: # non sweep mode
        time.sleep(self.measurementTime)
        measurementLine = numpy.array([self.V, self.I, time.time()-self.t0, self.status])                    
        return measurementLine
    elif command == ":source:voltage:step?":
      dV = (self.sweepEnd - self.sweepStart)/self.nPoints
      return numpy.array([dV])
  def close(self):
    pass 