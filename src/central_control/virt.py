import mpmath
import time
import numpy

class motion():
  def __init__(self, *args, **kwargs):
    print(f"virtual motion init args={args}, kwargs={kwargs}")
    addr = kwargs['address']
    content = addr.lstrip('us://')
    pieces = content.split('/')
    expected_lengths_in_mm = pieces[0]
    self.naxis = len(expected_lengths_in_mm.split(','))
    self.loc = [14.1]*self.naxis
  def connect(self):
    print ("Connected to virtual motion controller")
    return 0
  def move(self, mm):
    print("Virtually moving {:}mm".format(mm))
    for i,m in enumerate(mm):
      self.loc[i] = self.loc[i] + m
    return 0
  def goto(self, mm):
    print("Virtually moving to {:}mm".format(mm))
    self.loc = mm
    return 0
  def home(self):
    print("Virtually homing")
    return 0
  def estop(self):
    return 0
  def get_position(self):
    return self.loc

class illumination():
  runtime = 60
  def __init__(self, *args, **kwargs):
    print ("Init for virt illumination device")
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
    return spec 
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
  idn = 'Virtual Sourcemeter'
  nplc = 1
  def __init__(self, *args, **kwargs):
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
    self.nplc = args[0]

  def getNPLC(self, *args, **kwargs):
    return self.nplc

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

# dummy spectrum data to return (actual data from wavelabs)
spec = ([330.0,
  332.96875,
  335.9375,
  338.90625,
  341.875,
  344.84375,
  347.8125,
  350.78125,
  353.75,
  356.71875,
  359.6875,
  362.65625,
  365.625,
  368.59375,
  371.5625,
  374.53125,
  377.5,
  380.46875,
  383.4375,
  386.40625,
  389.375,
  392.34375,
  395.3125,
  398.28125,
  401.25,
  404.21875,
  407.1875,
  410.15625,
  413.125,
  416.09375,
  419.0625,
  422.03125,
  425.0,
  427.96875,
  430.9375,
  433.90625,
  436.875,
  439.84375,
  442.8125,
  445.78125,
  448.75,
  451.71875,
  454.6875,
  457.65625,
  460.625,
  463.59375,
  466.5625,
  469.53125,
  472.5,
  475.46875,
  478.4375,
  481.40625,
  484.375,
  487.34375,
  490.3125,
  493.28125,
  496.25,
  499.21875,
  502.1875,
  505.15625,
  508.125,
  511.09375,
  514.0625,
  517.03125,
  520.0,
  522.96875,
  525.9375,
  528.90625,
  531.875,
  534.84375,
  537.8125,
  540.78125,
  543.75,
  546.71875,
  549.6875,
  552.65625,
  555.625,
  558.59375,
  561.5625,
  564.53125,
  567.5,
  570.46875,
  573.4375,
  576.40625,
  579.375,
  582.34375,
  585.3125,
  588.28125,
  591.25,
  594.21875,
  597.1875,
  600.15625,
  603.125,
  606.09375,
  609.0625,
  612.03125,
  615.0,
  617.96875,
  620.9375,
  623.90625,
  626.875,
  629.84375,
  632.8125,
  635.78125,
  638.75,
  641.71875,
  644.6875,
  647.65625,
  650.625,
  653.59375,
  656.5625,
  659.53125,
  662.5,
  665.46875,
  668.4375,
  671.40625,
  674.375,
  677.34375,
  680.3125,
  683.28125,
  686.25,
  689.21875,
  692.1875,
  695.15625,
  698.125,
  701.09375,
  704.0625,
  707.03125,
  710.0,
  712.96875,
  715.9375,
  718.90625,
  721.875,
  724.84375,
  727.8125,
  730.78125,
  733.75,
  736.71875,
  739.6875,
  742.65625,
  745.625,
  748.59375,
  751.5625,
  754.53125,
  757.5,
  760.46875,
  763.4375,
  766.40625,
  769.375,
  772.34375,
  775.3125,
  778.28125,
  781.25,
  784.21875,
  787.1875,
  790.15625,
  793.125,
  796.09375,
  799.0625,
  802.03125,
  805.0,
  807.96875,
  810.9375,
  813.90625,
  816.875,
  819.84375,
  822.8125,
  825.78125,
  828.75,
  831.71875,
  834.6875,
  837.65625,
  840.625,
  843.59375,
  846.5625,
  849.53125,
  852.5,
  855.46875,
  858.4375,
  861.40625,
  864.375,
  867.34375,
  870.3125,
  873.28125,
  876.25,
  879.21875,
  882.1875,
  885.15625,
  888.125,
  891.09375,
  894.0625,
  897.03125,
  900.0,
  902.96875,
  905.9375,
  908.90625,
  911.875,
  914.84375,
  917.8125,
  920.78125,
  923.75,
  926.71875,
  929.6875,
  932.65625,
  935.625,
  938.59375,
  941.5625,
  944.53125,
  947.5,
  950.46875,
  953.4375,
  956.40625,
  959.375,
  962.34375,
  965.3125,
  968.28125,
  971.25,
  974.21875,
  977.1875,
  980.15625,
  983.125,
  986.09375,
  989.0625,
  992.03125,
  995.0,
  997.96875,
  1000.9375,
  1003.90625,
  1006.875,
  1009.84375,
  1012.8125,
  1015.78125,
  1018.75,
  1021.71875,
  1024.6875,
  1027.65625,
  1030.625,
  1033.59375,
  1036.5625,
  1039.53125,
  1042.5,
  1045.46875,
  1048.4375,
  1051.40625,
  1054.375,
  1057.34375,
  1060.3125,
  1063.28125,
  1066.25,
  1069.21875,
  1072.1875,
  1075.15625,
  1078.125,
  1081.09375,
  1084.0625,
  1087.03125],
 [98.0546875,
  100.17578125,
  95.76953125,
  78.8203125,
  80.2421875,
  57.89453125,
  71.53515625,
  77.41796875,
  84.69921875,
  187.91796875,
  456.91015625,
  624.26171875,
  969.203125,
  1076.01171875,
  1076.0078125,
  1025.87890625,
  1020.41796875,
  1321.7734375,
  1592.00390625,
  2103.54296875,
  2251.84375,
  2204.5,
  1814.2734375,
  1622.6484375,
  1575.57421875,
  1850.40234375,
  3117.87109375,
  5016.8515625,
  5896.8046875,
  6827.8515625,
  6745.66015625,
  5732.13671875,
  4331.87109375,
  3738.55859375,
  3003.42578125,
  2839.27734375,
  2900.2109375,
  3228.66015625,
  3425.2109375,
  3830.2578125,
  4071.1875,
  4774.875,
  5810.08984375,
  6380.40625,
  7377.78515625,
  7696.40234375,
  7734.125,
  7033.515625,
  6504.1875,
  5393.9921875,
  4526.1796875,
  4217.46875,
  3834.640625,
  3731.40625,
  3661.6171875,
  3711.29296875,
  3757.7890625,
  3817.328125,
  3890.4375,
  3929.984375,
  3995.1796875,
  4076.4609375,
  4133.16796875,
  4284.5234375,
  4337.65625,
  4480.140625,
  4587.58203125,
  4642.078125,
  4675.7421875,
  4673.59765625,
  4629.9609375,
  4509.80078125,
  4374.8828125,
  4311.71484375,
  4239.328125,
  4203.5625,
  4186.1796875,
  4161.9453125,
  4174.734375,
  4161.8125,
  4162.58203125,
  4127.1953125,
  4066.10546875,
  4036.1875,
  3940.84375,
  3828.80078125,
  3734.859375,
  3701.17578125,
  3686.43359375,
  3711.640625,
  3743.81640625,
  3826.92578125,
  3978.0859375,
  4136.80078125,
  4204.60546875,
  4373.23828125,
  4500.02734375,
  4561.453125,
  4559.39453125,
  4515.13671875,
  4414.08984375,
  4254.19921875,
  4022.4375,
  3912.83203125,
  3684.09765625,
  3480.7421875,
  3288.94921875,
  3216.64453125,
  3135.68359375,
  3125.80078125,
  3139.4921875,
  3231.31640625,
  3354.73828125,
  3462.640625,
  3606.38671875,
  3713.359375,
  3726.71875,
  3613.23046875,
  3387.62890625,
  3261.82421875,
  2956.87890625,
  2683.16796875,
  2446.234375,
  2284.95703125,
  2143.7265625,
  1982.5390625,
  1820.71875,
  1746.765625,
  2928.8515625,
  2942.18359375,
  3102.0,
  3242.4140625,
  3426.26171875,
  3771.65625,
  3911.2265625,
  3997.08984375,
  3990.8203125,
  3898.703125,
  3619.265625,
  3469.5859375,
  3349.55859375,
  3285.46875,
  3345.66015625,
  3464.5859375,
  3810.109375,
  3967.140625,
  4107.10546875,
  4071.45703125,
  3959.125,
  3614.41015625,
  3461.109375,
  3341.30859375,
  3423.0078125,
  3593.01953125,
  4164.15234375,
  4536.31640625,
  5394.65625,
  5780.234375,
  6162.02734375,
  6084.484375,
  5854.09375,
  4943.6015625,
  4372.0078125,
  3224.453125,
  2740.14453125,
  2006.35546875,
  1751.8046875,
  1526.96484375,
  1216.421875,
  1117.515625,
  982.0859375,
  946.93359375,
  914.21875,
  908.0234375,
  957.5234375,
  992.9921875,
  1037.43359375,
  1043.625,
  1006.6171875,
  969.9296875,
  884.67578125,
  836.7265625,
  735.4453125,
  706.359375,
  646.140625,
  619.98828125,
  582.68359375,
  557.1484375,
  509.89453125,
  487.1875,
  461.7734375,
  453.77734375,
  417.671875,
  402.09375,
  401.5390625,
  419.546875,
  428.23046875,
  418.984375,
  424.671875,
  434.2890625,
  424.921875,
  416.08203125,
  413.94921875,
  393.015625,
  387.98828125,
  405.71875,
  383.87890625,
  381.69140625,
  377.21484375,
  380.07421875,
  347.76953125,
  344.5234375,
  339.08984375,
  326.09375,
  313.95703125,
  323.05078125,
  296.8046875,
  272.12109375,
  287.52734375,
  253.32421875,
  226.9921875,
  219.03125,
  215.61328125,
  199.65625,
  171.21875,
  173.31640625,
  182.72265625,
  167.40625,
  154.19140625,
  165.3203125,
  175.640625,
  172.9140625,
  154.44140625,
  167.83984375,
  169.15625,
  163.4765625,
  144.01953125,
  136.86328125,
  135.640625,
  142.90625,
  123.91015625,
  107.26171875,
  110.2421875,
  114.65234375,
  98.17578125,
  86.140625,
  74.7734375,
  90.97265625,
  76.65625,
  71.09375,
  67.83203125,
  69.8984375,
  59.78515625,
  46.9375,
  32.2421875,
  30.9765625])
