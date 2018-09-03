from toolkit import wavelabs
from toolkit import k2400
from toolkit import put_ftp
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
  outputFormatRevision = 1  # tells reader what format to expect for the output file
  ssVocDwell = 10  # [s] dwell time for steady state voc determination
  ssIscDwell = 10  # [s] dwell time for steady state isc determination

  # start/end sweeps this many percentage points beyond Voc
  # bigger numbers here give better fitting for series resistance
  # at an incresed danger of pushing too much current through the device
  percent_beyond_voc = 50  

  # this is the datatype for the measurement in the h5py file
  measurement_datatype = np.dtype({'names': ['v','i','t','s'], 'formats': ['f', 'f', 'f', 'u4'], 'titles': ['Voltage [V]', 'Current [A]', 'Time [s]', 'Status bitmask']})

  # this is the datatype for the status messages in the h5py file
  status_datatype = np.dtype({'names': ['i', 'm'], 'formats': ['u4', h5py.special_dtype(vlen=str)], 'titles': ['Index', 'Message']})

  # this is an internal datatype to store the region of interest info
  roi_datatype = np.dtype({'names': ['s', 'e', 'd'], 'formats': ['u4', 'u4', object], 'titles': ['Start Index', 'End Index', 'Description']})

  m = np.array([], dtype=measurement_datatype)  # measurement list: columns = v, i, timestamp, status
  s = np.array([], dtype=status_datatype)  # status list: columns = corresponding measurement index, status message
  r = np.array([], dtype=roi_datatype)  # list defining regions of interest in the measurement list

  adapterBoardTypes = ['Unknown', '28x28 Snaith Legacy', '30x30', '28x28 MRG', '25x25 DBG']
  layoutTypes = ['Unknown', '30x30 Two Big', '30x30 One Big', '30x30 Six Small', '28x28 Snaith Legacy', '28x28 MRG', '25x25 DBG-A', '25x25 DBG-B', '25x25 DBG-C', '25x25 DBG-D', '25x25 DBG-E']

  def __init__(self, saveDir):
    self.saveDir = saveDir
    self.software_commit_hash = logic.getMyHash()
    print('Software commit hash: {:s}'.format(self.software_commit_hash))

  def __setattr__(self, attr, value):
    """here we can override what happends when we set an attribute"""
    if attr == 'Voc':
      self.__dict__[attr] = value
      if value != None:
        print('V_oc is {:.4f}mV'.format(value*1000))

    elif attr == 'Isc':
      self.__dict__[attr] = value
      if value != None:
        print('I_sc is {:.4f}mA'.format(value*1000))      

    else:
      self.__dict__[attr] = value

  def connect(self, dummy=False, visa_lib='@py', visaAddress='GPIB0::24::INSTR', pcbAddress='10.42.0.54', pcbPort=23, terminator='\n', serialBaud=57600, no_wavelabs=False):
    """Forms a connection to the PCB, the sourcemeter and the light engine
    will form connections to dummy instruments if dummy=true
    """

    if dummy:
      self.sm = virt.k2400()
      self.pcb = virt.pcb()
    else:
      self.sm = k2400(visa_lib=visa_lib, terminator=terminator, addressString=visaAddress, serialBaud=serialBaud)
      self.pcb = pcb(ipAddress=pcbAddress, port=pcbPort)      

    if no_wavelabs:
      self.wl = self.wl = virt.wavelabs()
    else:
      self.wl = wavelabs()
      self.wl.startServer()
      self.wl.awaitConnection()
      self.wl.activateRecipe()

  def getMyHash(short=True):
    thisPath = os.path.dirname(os.path.abspath(__file__))
    projectPath = os.path.join(thisPath, os.path.pardir)
    HEADFile = os.path.join(projectPath, '.git', 'HEAD')
    commit_hashFile = os.path.join(projectPath, 'commit_hash.txt')

    myHash = 'Unknown'
    if os.path.exists(HEADFile): # are we in a git repo?
      f = open(HEADFile)
      hashFileLocation = f.readline().splitlines()[0].split()[1].split('/')
      f.close()
      f = open(os.path.join(projectPath, '.git', *hashFileLocation))
      myHash = f.readline().splitlines()[0]
      f.close()
    elif os.path.exists(commit_hashFile):  # no git repo? check in commit_hash.txt
      f = open(commit_hashFile)
      contents = f.readline().splitlines()[0].split()
      f.close()
      if len(contents) != 3:  # the length will be 3 here if it doesn't contain the hash as position [1]
        myHash = contents[1]

    if short:
      myHash = myHash[:7]
    return myHash

  def hardwareTest(self):
    self.wl.startRecipe()


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

    print("LED test mode active on substrate(s) {:s}".format(self.pcb.substratesConnected))
    print("Every pixel should get an LED pulse IV sweep now, plus the light should turn on")    
    for substrate in self.pcb.substratesConnected:
      sweepHigh = 0.01 # amps
      sweepLow = 0 # amps

      self.pcb.pix_picker(substrate, 1)
      self.sm.setNPLC(0.01)
      self.sm.setupSweep(sourceVoltage=False, compliance=2.5, nPoints=101, start=sweepLow, end=sweepHigh)
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

    self.wl.cancelRecipe()

  def measureIntensity(self):
    """returns number of suns """
    oneSunCounts = 1
    adcChan = 2
    countsA = self.pcb.getADCCounts(adcChan)

    adcChan = 3
    countsB = self.pcb.getADCCounts(adcChan)
    return ((countsA+countsB)/(2))/oneSunCounts

  def lookupAdapterBoard(self, counts):
    """map resistor divider adc counts to adapter board type"""
    # TODO: write this

    if counts > 100:
      board_index = 0
    else:
      board_index = 0

    return(board_index)

  def lookupPixelArea(self, pixel_index):
    """return pixel area in sq cm given pixel index (also needs sample_layout_type from self)"""
    # TODO: write this

    if self.sample_layout_type == 1:
      if pixel_index == 1:
        area = 1.0
      elif pixel_index == 2:
        area = 1.0
      else:
        area = 1.0
    else:
      area = 1.0

    # take area from cli input (if it's provided)
    if hasattr(self, 'cli_area'):
      area = self.cli_area

    return(area)

  def runSetup(self, operator):
    self.wl.startRecipe()
    self.run_dir = self.slugify(operator) + '-' + time.strftime('%y-%m-%d')
    destinationDir = os.path.join(self.saveDir, self.run_dir)
    if not os.path.exists(destinationDir):
      os.makedirs(destinationDir)

    i = 0
    genFullpath = lambda a: os.path.join(destinationDir,"Run{:d}.h5".format(a))
    while os.path.exists(genFullpath(i)):
      i += 1    
    self.f = h5py.File(genFullpath(i),'x')
    print("Creating file {:}".format(self.f.filename))
    self.f.attrs['Operator'] = np.string_(operator)
    self.f.attrs['Timestamp'] = time.time()
    self.f.attrs['PCB Firmware Hash'] = np.string_(self.pcb.get('v'))
    self.f.attrs['Software Hash'] = np.string_(self.software_commit_hash)
    self.f.attrs['Format Revision'] = np.int(self.outputFormatRevision)
    self.f.attrs['Intensity [suns]'] = np.float(self.measureIntensity())

  def runDone(self):
    self.wl.cancelRecipe()
    print("\nClosing {:s}".format(self.f.filename))
    this_filename = self.f.filename
    self.f.close()
    
    ftp = put_ftp('epozz')
    fp = open(this_filename,'rb')
    ftp.uploadFile(fp,'/drop/' + self.run_dir + '/')
    ftp.close()
    fp.close()
    
  def substrateSetup (self, position, suid='', description='', sampleLayoutType = 0):
    self.position = position
    self.pcb.pix_picker(position, 0)
    self.f.create_group(position)

    self.f[position].attrs['Sample Unique Identifier'] = np.string_(suid)
    self.f[position].attrs['Sample Description'] = np.string_(description)

    abCounts = self.pcb.getADCCounts(position)
    self.f[position].attrs['Sample Adapter Board ADC Counts'] = abCounts
    self.adapter_board_index = self.lookupAdapterBoard(abCounts)
    self.f[position].attrs['Sample Adapter Board'] = np.string_(self.adapterBoardTypes[self.adapter_board_index])
    self.f[position].attrs['Sample Layout Type'] = np.string_(self.layoutTypes[sampleLayoutType])
    self.sample_layout_type = sampleLayoutType

  def pixelSetup(self, pixel, t_dwell_voc=10):
    """Call this to switch to a new pixel"""
    self.pixel = str(pixel)
    self.area = self.lookupPixelArea(pixel)
    self.pcb.pix_picker(self.position, pixel)
    self.f[self.position].create_group(self.pixel)
    self.f[self.position+'/'+self.pixel].attrs['area'] = self.area  # in cm^2

    vocs = self.steadyState(t_dwell=t_dwell_voc, NPLC=10, sourceVoltage=False, compliance=2, senseRange='a', setPoint=0)

    self.Voc = vocs[-1][0]  # take the last measurement to be Voc

    self.f[self.position+'/'+self.pixel].attrs['Voc'] = self.Voc
    self.addROI(0, len(vocs) - 1, 'V_oc dwell')
    #self.f[self.position+'/'+self.pixel].create_dataset('VocDwell', data=vocs)

    # derive connection polarity
    #if self.Voc < 0:
        #vPol = -1
        #iPol = 1
    #else:
        #vPol = 1
        #iPol = -1

  def pixelComplete (self):
    """Call this when all measurements for a pixel are complete"""
    self.pcb.pix_picker(self.position, 0)
    m = self.f[self.position+'/'+self.pixel].create_dataset('all_measurements', data=self.m, compression="gzip")
    for i in range(len(self.r)):
      m.attrs[self.r[i][2]] = m.regionref[self.r[i][0]:self.r[i][1]]
    self.f[self.position+'/'+self.pixel].create_dataset('status_list', data=self.s, compression="gzip")
    self.m = np.array([], dtype=self.measurement_datatype)  # reset measurement storage
    self.s = np.array([], dtype=self.status_datatype)  # reset status storage
    self.r = np.array([], dtype=self.roi_datatype)  # reset region of interest
    self.Voc = None
    self.Isc = None

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
    """adds status message to the status message list"""
    print(message)
    s = np.array((len(self.m), message), dtype=self.status_datatype)
    self.s = np.append(self.s, s)

  def addROI(self, start, stop, description):
    """adds a region of interest to the measurement list
    takes start index, stop index and roi description"""
    print("New region of iterest: [{:},{:}]\t{:s}".format(start, stop, description))
    r = np.array((start, stop, description), dtype=self.roi_datatype)
    self.r = np.append(self.r, r)  

  def steadyState(self, t_dwell=10, NPLC=10, sourceVoltage=True, compliance=0.04, setPoint=0, senseRange='f'):
    """ makes steady state measurements for t_dwell seconds
    set NPLC to -1 to leave it unchanged
    returns array of measurements
    """
    self.insertStatus('Measuring steady state {:s} at {:.0f} m{:s}'.format('current' if sourceVoltage else 'voltage', setPoint*1000, 'V' if sourceVoltage else 'A'))
    if NPLC != -1:
      self.sm.setNPLC(NPLC)
    self.sm.setupDC(sourceVoltage=sourceVoltage, compliance=compliance, setPoint=setPoint, senseRange=senseRange)
    self.sm.write(':arm:source immediate') # this sets up the trigger/reading method we'll use below
    q = self.sm.measureUntil(t_dwell=t_dwell)
    qa = np.array([tuple(s) for s in q], dtype=self.measurement_datatype)
    self.m = np.append(self.m, qa)
    return qa

  def sweep(self, sourceVoltage=True, senseRange='f', compliance=0.04, nPoints=1001, stepDelay=0.005, start=1, end=0, NPLC=1, message=None):

    self.sm.setNPLC(NPLC)
    self.sm.setupSweep(sourceVoltage=sourceVoltage, compliance=compliance, nPoints=nPoints, stepDelay=stepDelay, start=start, end=end, senseRange=senseRange)

    if message == None:
      word ='current' if sourceVoltage else 'voltage'
      abv = 'V' if sourceVoltage else 'A'
      message = 'Sweeping {:s} from {:.0f} m{:s} to {:.0f} m{:s}'.format(word, start, abv, end, abv)
    self.insertStatus(message)
    raw = self.sm.measure()
    sweepValues = np.array(list(zip(*[iter(raw)]*4)), dtype=self.measurement_datatype)
    self.m = np.append(self.m, sweepValues)

    return sweepValues
