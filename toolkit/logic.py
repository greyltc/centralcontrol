from toolkit import k2400
from toolkit import pcb
from toolkit import virt
import h5py
import numpy as np
import unicodedata
import re
import os
import time

class logic:
  """ this class contains the sourcemeter and pcb control logic
  """
  ssVocDwell = 10  # [s] dwell time for steady state voc determination
  ssIscDwell = 10  # [s] dwell time for steady state isc determination 51398016 40-24-10
  
  measurement_datatype = np.dtype({'names': ['v','i','t','s'], 'formats': ['f', 'f', 'f', 'u4'], 'titles': ['Voltage [V]', 'Current [A]', 'Time [s]', 'Status bitmask']})
  status_datatype = np.dtype({'names': ['i', 'm'], 'formats': ['u4', h5py.special_dtype(vlen=str)], 'titles': ['Index', 'Message']})
  #np.dtype([('index', np.uint32), ('message', h5py.special_dtype(vlen=str))])

  m = np.array([], dtype=measurement_datatype)  # measurement list: columns = v, i, timestamp, status
  s = np.array([], dtype=status_datatype)  # status list: columns = corresponding measurement index, status message

  adapterBoardTypes = ['Unknown', '28x28 Snaith Legacy', '30x30', '28x28 MRG', '25x25 DBG']
  layoutTypes = ['Unknown', '30x30 Two Big', '30x30 One Big', '30x30 Six Small', '28x28 Snaith Legacy', '28x28 MRG', '25x25 DBG-A', '25x25 DBG-B', '25x25 DBG-C', '25x25 DBG-D', '25x25 DBG-E']

  def __init__(self, saveDir):
    self.saveDir = saveDir
  
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
      
  def lookupAdapterBoard(self, counts):
    """map resistor divider adc counts to adapter board type"""

    return(self.adapterBoardTypes[0])
  
  def runSetup(self, operator):
    destinationDir = os.path.join(self.saveDir, self.slugify(operator) + '-' + time.strftime('%y-%m-%d'))
    if not os.path.exists(destinationDir):
      os.makedirs(destinationDir)
      
    i = 0
    genFullpath = lambda a: os.path.join(destinationDir,"Run{:d}.h5".format(a))
    while os.path.exists(genFullpath(i)):
      i += 1    
    self.f = h5py.File(genFullpath(i),'x')
    #self.f.attrs.create('Operator', np.string_(operator))
    self.f.attrs['Operator'] = np.string_(operator)
    self.f.attrs['Timestamp'] = time.time()
    self.f.attrs['PCB Firmware Hash'] = np.string_(self.pcb.get('v'))
    self.f.attrs['Software Hash'] = np.string_("Not implemented")  # TODO: figure out how to get software version here
    
  def runDone(self):
    self.f.close()
      
  def substrateSetup (self, position, suid='', description='', sampleLayoutType = 0):
    self.position = position
    self.pcb.pix_picker(position, 0)
    self.f.create_group(position)

    self.f[position].attrs['Sample Unique Identifier'] = np.string_(suid)
    self.f[position].attrs['Sample Description'] = np.string_(description)
    
    abCounts = self.pcb.getADCCounts(position)
    self.f[position].attrs['Sample Adapter Board ADC Counts'] = abCounts
    self.f[position].attrs['Sample Adapter Board'] = np.string_(self.lookupAdapterBoard(abCounts))
    self.f[position].attrs['Sample Layout Type'] = np.string_(self.layoutTypes[sampleLayoutType])
    
  def pixelSetup(self, pixel, t_dwell_voc=10):
    """Call this to switch to a new pixel"""
    self.pixel = str(pixel)
    self.pcb.pix_picker(self.position, pixel)
    self.f[self.position].create_group(self.pixel)
    
    vocs = self.steadyState(t_dwell= t_dwell_voc, NPLC=10, sourceVoltage=False, compliance=2, setPoint=0)
    
    self.Voc = vocs[-1][0]  # take the last measurement to be Voc
    
    self.f[self.position+'/'+self.pixel].attrs['Voc'] = self.Voc
    self.f[self.position+'/'+self.pixel].create_dataset('VocDwell', data=vocs)
    
    # derive connection polarity
    if self.Voc < 0:
        vPol = -1
        iPol = 1
    else:
        vPol = 1
        iPol = -1
    
  def pixelComplete (self):
    """Call this when all measurements for a pixel are complete"""
    self.pcb.pix_picker(self.position, 0)
    self.f[self.position+'/'+self.pixel].create_dataset('AllMeasurements', data=self.m)
    self.f[self.position+'/'+self.pixel].create_dataset('StatusList', data=self.s)
    self.m = np.array([], dtype=self.measurement_datatype)
    self.s = np.array([], dtype=self.status_datatype)
    self.Voc = None
    
  def slugify(self, value, allow_unicode=False):
    """
    Convert to ASCII if 'allow_unicode' is False. Convert spaces to hyphens.
    Remove characters that aren't alphanumerics, underscores, or hyphens.
    Convert to lowercase. Also strip leading and trailing whitespace.
    """
    value = str(value)
    if allow_unicode:
      value = unicodedata.normalize('NFKC', value)
    else:
      value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value).strip().lower()
    return re.sub(r'[-\s]+', '-', value)

  def insertStatus(self, message):
    print(message)
    s = np.array((len(self.m), message), dtype=self.status_datatype)
    self.s = np.append(self.s, s)
      
  def steadyState(self, t_dwell=10, NPLC=10, sourceVoltage=False, compliance=2, setPoint=0):
    """ makes steady state measurements for t_dwell seconds
    set NPLC to -1 to leave it unchanged
    returns array of measurements
    """
    self.insertStatus('Measuring steady state {:s} at {:.0f} m{:s}'.format('current' if sourceVoltage else 'voltage', setPoint*1000, 'V' if sourceVoltage else 'A'))
    if NPLC != -1:
      self.sm.setNPLC(NPLC)
    self.sm.setupDC(sourceVoltage=sourceVoltage, compliance=compliance, setPoint=setPoint)
    self.sm.write(':arm:source immediate') # this sets up the trigger/reading method we'll use below
    q = self.sm.measureUntil(t_dwell=t_dwell)
    qa = np.array([tuple(s) for s in q], dtype=self.measurement_datatype)
    self.m = np.append(self.m, qa)
    return qa

  def sweep(self, sourceVoltage=True, compliance=0.04, nPoints=1001, stepDelay=-1, start=1, end=0, NPLC=1, message=None):
    
    self.sm.setNPLC(NPLC)
    self.sm.setupSweep(sourceVoltage=sourceVoltage, compliance=compliance, nPoints=nPoints, stepDelay=stepDelay, start=start, end=end)

    if message == None:
      word ='current' if sourceVoltage else 'voltage'
      abv = 'V' if sourceVoltage else 'A'
      message = 'Sweeping {:s} from {:.0f} m{:s} to {:.0f} m{:s}'.format(word, start, abv, end, abv)
    self.insertStatus(message)
    raw = self.sm.measure()
    sweepValues = np.array(list(zip(*[iter(raw)]*4)), dtype=self.measurement_datatype)
    self.m = np.append(self.m, sweepValues)

    return sweepValues