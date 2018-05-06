from toolkit import k2400
from toolkit import pcb
from toolkit import virt

class logic:
  """ this class contains the sourcemeter and pcb control logic
  """
  ssVocDwell = 10  # [s] dwell time for steady state voc determination
  ssIscDwell = 10  # [s] dwell time for steady state isc determination
  
  m = np.array([])  # measurement list
  s = []  # status list
  ns = np.array([])  # list of measurement indicies for the status messages
  
  def __init__(self):
    pass
  
  def connect(self, dummy=False, visa_lib='@py', visaAddress='GPIB0::24::INSTR', pcbAddress='10.42.0.54', pcbPort=23, terminator='\n', serialBaud=57600):
    """Forms a connection to the PCB and the sourcemeter
    will form connections to dummy instruments if dummy=true
    """

    if dummy:
      self.sm = virt.k2400()
      self.pcb = virt.pcb()
    else:
      self.sm = k2400(visa_lib=visa_lib, terminator=terminator, addressString=visaAddress, serialBaud=serialBaud)
      self.pcb = pcb(ipAddress=pcbAddress, port=pcbPort)

  def hardwareTest(self):
    print("LED test mode active on substrate(s) {:s}".format(self.pcb.substratesConnected))
    print("Every pixel should get an LED pulse IV sweep now")
    for substrate in self.pcb.substratesConnected:
      sweepHigh = 0.01 # amps
      sweepLow = 0 # amps
    
      self.pcb.pix_picker(substrate, 1)
      self.sm.setNPLC(0.01)
      self.sm.setupSweep(sourceVoltage=False, compliance=2.5, nPoints=101, stepDelay=-1, start=sweepLow, end=sweepHigh)
      self.sm.write(':arm:source bus') # this allows for the trigger style we'll use here
    
      for pix in range(8):
        print(substrate+str(pix+1))
        if pix != 0:
          self.pcb.pix_picker(substrate,pix+1)
    
        self.sm.updateSweepStart(sweepLow)
        self.sm.updateSweepStop(sweepHigh)
        self.sm.arm()
        self.sm.trigger()
        self.sm.opc()
    
        self.sm.updateSweepStart(sweepHigh)
        self.sm.updateSweepStop(sweepLow)
        self.sm.arm()
        self.sm.trigger()
        self.sm.opc()
    
        # off during pix switchover
        self.sm.setOutput(0)
    
      self.sm.outOn(False)
    
      # deselect all pixels
      self.pcb.pix_picker(substrate, 0)
    
    # exercise pcb ADC
    print('ADC Counts:')
    adcChan = 2
    counts = self.pcb.getADCCounts(adcChan)
    print('{:d}\t<-- D1 Diode (TP3, AIN{:d}): '.format(counts, adcChan))
    
    adcChan = 3
    counts = self.pcb.getADCCounts(adcChan)
    print('{:d}\t<-- D2 Diode (TP4, AIN{:d})'.format(counts, adcChan))
    
    adcChan = 0
    counts = self.pcb.getADCCounts(adcChan)
    print('{:d}\t<-- Adapter board resistor divider (TP5, AIN{:d})'.format(counts, adcChan))
    
    adcChan = 1
    counts = self.pcb.getADCCounts(adcChan)
    print('{:d}\t<-- Disconnected (TP2, AIN{:d})'.format(counts, adcChan))
    
    adcChan = 0
    for substrate in self.pcb.substratesConnected:
      counts = self.pcb.getADCCounts(substrate)
      print('{:d}\t<-- Substrate {:s} adapter board resistor divider (TP5, AIN{:d})'.format(counts, substrate, adcChan))
      
  def insertStatus(self, message):
    self.s.append(message)
    self.ns.append(len(self.m))
    
      
  def steadyState(self, t_dwell=10, NPLC=10, sourceVoltage=False, compliance=2, setPoint=0):
    """ makes steady state measurements for t_dwell seconds
    set NPLC to -1 to leave it unchanged
    returns array of measurements
    """
    self.insertStatus('steady state {:s} measurement at {:.0} m{:s}'.format('current' if sourceVoltage else 'voltage', setPoint*1000, 'A' if sourceVoltage else 'V'))
    if NPLC != -1:
      self.sm.setNPLC(NPLC)
    self.sm.setupDC(sourceVoltage=sourceVoltage, compliance=compliance, setPoint=setPoint)
    self.sm.write(':arm:source immediate') # this sets up the trigger/reading method we'll use below
    q = self.sm.measureUntil(t_dwell=t_dwell)
    qa = np.array(q)
    self.m.append(qa)
    return qa