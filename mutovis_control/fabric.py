import h5py
import numpy as np
import unicodedata
import re
import os
import time
import tempfile
from collections import deque

import mutovis_control as mc

class fabric:
  """ this class contains the sourcemeter and pcb control logic
  """
  outputFormatRevision = "1.6.0"  # tells reader what format to expect for the output file
  ssVocDwell = 10  # [s] dwell time for steady state voc determination
  ssIscDwell = 10  # [s] dwell time for steady state isc determination

  # start/end sweeps this many percentage points beyond Voc
  # bigger numbers here give better fitting for series resistance
  # at an incresed danger of pushing too much current through the device
  percent_beyond_voc = 50
  
  # guess at what the current limit should be set to (in amps) if we have no other way to determine it
  compliance_guess = 0.04

  # this is the datatype for the measurement in the h5py file
  measurement_datatype = np.dtype({'names': ['voltage','current','time','status'], 'formats': ['f', 'f', 'f', 'u4'], 'titles': ['Voltage [V]', 'Current [A]', 'Time [s]', 'Status bitmask']})

  # this is the datatype for the status messages in the h5py file
  status_datatype = np.dtype({'names': ['index', 'message'], 'formats': ['u4', h5py.special_dtype(vlen=str)], 'titles': ['Index', 'Message']})

  # this is an internal datatype to store the region of interest info
  roi_datatype = np.dtype({'names': ['start_index', 'end_index', 'description'], 'formats': ['u4', 'u4', object], 'titles': ['Start Index', 'End Index', 'Description']})

  m = np.array([], dtype=measurement_datatype)  # measurement list: columns = v, i, timestamp, status
  s = np.array([], dtype=status_datatype)  # status list: columns = corresponding measurement index, status message
  r = np.array([], dtype=roi_datatype)  # list defining regions of interest in the measurement list
  
  # function to use when sending ROIs to the GUI
  update_gui = None

  def __init__(self, saveDir, archive_address=None):
    self.saveDir = saveDir
    self.archive_address = archive_address
    
    self.software_commit_hash = fabric.getMyHash()
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

  def connect(self, dummy=False, visa_lib='@py', visaAddress='GPIB0::24::INSTR', pcbAddress='10.42.0.54:23', lightAddress=None, visaTerminator='\n', visaBaud=57600):
    """Forms a connection to the PCB, the sourcemeter and the light engine
    will form connections to dummy instruments if dummy=true
    """

    if dummy:
      self.sm = mc.virt.k2400()
      self.pcb = mc.virt.pcb()
    else:
      self.sm = mc.k2400(visa_lib=visa_lib, terminator=visaTerminator, addressString=visaAddress, serialBaud=visaBaud)
      self.pcb = mc.pcb(address=pcbAddress)
      
    self.mppt = mc.mppt(self.sm)

    if lightAddress == None:
      self.le = mc.virt.illumination()
    else:
      self.le = mc.illumination(address = lightAddress)
      self.le.connect()
      
    #if motionAddress == None:
      #self.me = mc.virt.motion()
    #else:
      #self.me = mc.motion(address = motionAddress)
      #self.me.connect()    

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

  def hardwareTest(self, substrates_to_test):
    self.le.on()
    
    n_adc_channels = 8
    
    for chan in range(n_adc_channels):
      print('ADC channel {:} Counts: {:}'.format(chan,self.pcb.getADCCounts(chan)))
      
    chan = 2
    counts = self.pcb.getADCCounts(chan)
    print('{:d}\t<-- D1 Diode ADC counts (TP3, AIN{:d})'.format(counts, chan))

    chan = 3
    counts = self.pcb.getADCCounts(chan)
    print('{:d}\t<-- D2 Diode ADC counts (TP4, AIN{:d})'.format(counts, chan))

    chan = 0
    for substrate in substrates_to_test:
      r = self.pcb.get('d'+substrate)
      print('{:s}\t<-- Substrate {:s} adapter resistor value in ohms (AIN{:d})'.format(r, substrate, chan))

    print("LED test mode active on substrate(s) {:s}".format(substrates_to_test))
    print("Every pixel should get an LED pulse IV sweep now, plus the light should turn on")    
    for substrate in substrates_to_test:
      sweepHigh = 0.01 # amps
      sweepLow = 0.0001 # amps
      
      ready_to_sweep = False
      
      for pix in range(8):
        print(substrate+str(pix+1))
        if self.pcb.pix_picker(substrate,pix+1):
          if not ready_to_sweep:  # setup the sourcemeter if this is our first pixel
            self.sm.setNPLC(0.01)
            self.sm.setupSweep(sourceVoltage=False, compliance=2.5, nPoints=101, start=sweepLow, end=sweepHigh)
            self.sm.write(':arm:source bus') # this allows for the trigger style we'll use here
            ready_to_sweep = True
  
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

      self.sm.outOn(False)

      # deselect all pixels
      self.pcb.pix_picker(substrate, 0)

    self.le.off()

  def measureIntensity(self, diode_cal):
    """
    returns number of suns and ADC counts for both diodes
    takes diode calibration values in diode_cal
    if diode_cal is not a tuple with valid calibration values, sets intensity to 1.0 sun for both diodes
    """
    ret = [self.pcb.get('p1'), self.pcb.get('p2'), 1.0, 1.0]
    
    if type(diode_cal) == list or type(diode_cal) == tuple:
      if diode_cal[0] <= 1:
        print("WARNING: No or bad intensity diode calibration values, assuming 1.0 suns")
      else:
        ret[2] = ret[0]/diode_cal[0]
        
      if diode_cal[1] <= 1:
        print("WARNING: No or bad intensity diode calibration values, assuming 1.0 suns")
      else:
        ret[3] = ret[1]/diode_cal[1]    
    
    return ret
    
  def lookupAdapterBoard(self, r):
    """map resistor value counts to adapter board type
    TODO: this should all be configured by the user in the prefrences file instead of hardcoded here
    """
    
    # adapterBoardTypes = ['Unknown', '28x28 Snaith Legacy', '30x30', '28x28 MRG', '25x25 DBG', '1x1in MIT', 'No Board']
    # layoutTypes = ['Unknown', '30x30 Two Big', '30x30 One Big', '30x30 Six Small', '28x28 Snaith Legacy', '28x28 MRG', '25x25 DBG-A', '25x25 DBG-B', '25x25 DBG-C', '25x25 DBG-D', '25x25 DBG-E', 'MIT 6x4.8x3.8mm']

    if r > 900 and r < 1100:
      self.adapterBoard = '28x28 Snaith Legacy'
      self.layout = '28x28 Snaith Legacy'
      #TODO enforce areas
      pixel_areas = [0.0909, 0.0909, 0.0909, 0.0909, 0.0909, 0.0909, 0.0909, 0.0909]
    elif r > 40000: # maximum is ~40k
      self.adapterBoard = 'No Board'
      self.layout = 'No Board'
    else:
      self.adapterBoard = 'Unknown, R={:}'.format(r)
      self.layout = 'Unknown board, R={:}'.format(r)

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

  def runSetup(self, operator, diode_cal, ignore_diodes=False):
    """
    stuff that needs to be done at the start of a run
    returns intensity tuple of length 4 where [0:1] are the raw ADC counts measured by the PCB's photodiodes
    and [2:3] are the number of suns of intensity
    if diode_cal == True, suns intensity will be assumed and reported as 1.0
    if type(diode_cal) == list, diode_cal[0] and [1] will be used to calculate number of suns
    if ignore_diodes == True, diode ADC values will not be read and intensity = (1, 1, 1.0 1.0) will be used and reported
    """
    self.run_dir = self.slugify(operator) + '-' + time.strftime('%y-%m-%d')
    
    if self.saveDir == None or self.saveDir == '__tmp__':
      td = tempfile.mkdtemp(suffix='_iv_data')
      # self.saveDir = td.name
      self.saveDir = td
      print('Using {:} as data storage location'.format(self.saveDir))
    
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
    self.f.attrs['Format Revision'] = np.string_(self.outputFormatRevision)
    self.le.on()
    if type(self.le) == mc.illumination:
      time.sleep(0.5) # if this is a real solar sim (not a virtual one), wait half a sec before measuring intensity
    if ignore_diodes == True:
      intensity = (1, 1, 1.0, 1.0)
    else:
      intensity = self.measureIntensity(diode_cal)
    self.f.attrs['Diode 1 intensity [ADC counts]'] = np.int(intensity[0])
    self.f.attrs['Diode 2 intensity [ADC counts]'] = np.int(intensity[1])
    if type(diode_cal) == list:
      self.f.attrs['Diode 1 calibration [ADC counts]'] = np.int(diode_cal[0])
      self.f.attrs['Diode 2 calibration [ADC counts]'] = np.int(diode_cal[1])
    else:  #  we re-calibrated this run
      self.f.attrs['Diode 1 calibration [ADC counts]'] = np.int(intensity[0])
      self.f.attrs['Diode 2 calibration [ADC counts]'] = np.int(intensity[1])
    self.f.attrs['Diode 1 intensity [suns]'] = np.float(intensity[2])
    self.f.attrs['Diode 2 intensity [suns]'] = np.float(intensity[3])
    print("Intensity = [{:0.4f} {:0.4f}] suns".format(np.float(intensity[2]), np.float(intensity[3])))
    return intensity

  def runDone(self):
    self.le.off()
    print("\nClosing {:s}".format(self.f.filename))
    this_filename = self.f.filename
    self.f.close()
    if self.archive_address is not None:
      if self.archive_address.startswith('ftp://'):
        with mc.put_ftp(self.archive_address+self.run_dir + '/', pasv=True) as ftp:
          with open(this_filename,'rb') as fp:
            ftp.uploadFile(fp)

      else:
        print('WARNING: Could not understand archive url')
    
  def substrateSetup (self, position, suid='', description='', sampleLayoutType = 0):
    self.position = position
    if self.pcb.pix_picker(position, 0):
      self.f.create_group(position)
  
      self.f[position].attrs['Sample Unique Identifier'] = np.string_(suid)
      self.f[position].attrs['Sample Description'] = np.string_(description)
  
      abResistor = int(self.pcb.get('d'+position))
      self.f[position].attrs['Sample Adapter Board Resistor Value'] = abResistor
      self.lookupAdapterBoard(abResistor)
      self.f[position].attrs['Sample Adapter Board'] = np.string_(self.adapterBoard)
      self.f[position].attrs['Sample Layout Type'] = np.string_(self.layout)
      self.sample_layout_type = sampleLayoutType
      return True
    else:
      return False

  def pixelSetup(self, pixel, t_dwell_voc=10):
    """Call this to switch to a new pixel"""
    self.pixel = str(pixel)
    if self.pcb.pix_picker(self.position, pixel):
      self.area = self.lookupPixelArea(pixel)
  
      self.f[self.position].create_group(self.pixel)
      self.f[self.position+'/'+self.pixel].attrs['area'] = self.area * 1e-4  # in m^2
  
      vocs = self.steadyState(t_dwell=t_dwell_voc, NPLC=10, sourceVoltage=False, compliance=2, senseRange='a', setPoint=0)
      self.registerMeasurements(vocs, 'V_oc dwell')
  
      self.Voc = vocs[-1][0]  # take the last measurement to be Voc
      self.mppt.Voc = self.Voc
  
      self.f[self.position+'/'+self.pixel].attrs['Voc'] = self.Voc
      return True
    else:
      return False

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
    self.mppt.reset()

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

  def registerMeasurements(self, measurements, description):
    """adds an array of measurements to the master list and creates an ROI for them
    takes new measurement numpy array and description of them"""
    roi = {}
    roi['v'] = [float(e[0]) for e in measurements]
    roi['i'] = [float(e[1]) for e in measurements]
    roi['t'] = [float(e[2]) for e in measurements]
    roi['s'] = [float(e[3]) for e in measurements]
    roi['message'] =  description
    roi['area'] =  self.area
    try:
      self.update_gui(roi)  # send the new region of interest data to the GUI
    except:
      pass  # probably no gui server to send data to, NBD
    self.m = np.append(self.m, measurements)
    length = len(measurements)
    if length > 0:
      stop = len(self.m) - 1
      start = stop - length + 1
      print("New region of iterest: [{:},{:}]\t{:s}".format(start, stop, description))
      r = np.array((start, stop, description), dtype=self.roi_datatype)
      self.r = np.append(self.r, r)
    else:
      print("WARNING: Non-positive ROI length")

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
    return qa

  def sweep(self, sourceVoltage=True, senseRange='f', compliance=0.04, nPoints=1001, stepDelay=0.005, start=1, end=0, NPLC=1, message=None):
    """ make a series of measurements while sweeping the sourcemeter along linearly progressing voltage or current setpoints
    """

    self.sm.setNPLC(NPLC)
    self.sm.setupSweep(sourceVoltage=sourceVoltage, compliance=compliance, nPoints=nPoints, stepDelay=stepDelay, start=start, end=end, senseRange=senseRange)

    if message == None:
      word ='current' if sourceVoltage else 'voltage'
      abv = 'V' if sourceVoltage else 'A'
      message = 'Sweeping {:s} from {:.0f} m{:s} to {:.0f} m{:s}'.format(word, start, abv, end, abv)
    self.insertStatus(message)
    raw = self.sm.measure()
    sweepValues = np.array(list(zip(*[iter(raw)]*4)), dtype=self.measurement_datatype)

    return sweepValues

  def track_max_power(self, duration=30, message=None, NPLC=-1):
    if message == None:
      message = 'Tracking maximum power point for {:} seconds'.format(duration)
    self.insertStatus(message)
    raw = self.mppt.launch_tracker(duration=duration, NPLC=NPLC)
    # raw = self.mppt.launch_tracker(duration=duration, callback=fabric.mpptCB, NPLC=NPLC)
    qa = np.array([tuple(s) for s in raw], dtype=self.measurement_datatype)
    self.registerMeasurements(qa, 'MPPT')
    
    if self.mppt.Vmpp != None:
      self.f[self.position+'/'+self.pixel].attrs['Vmpp'] = self.mppt.Vmpp
    if self.mppt.Impp != None:
      self.f[self.position+'/'+self.pixel].attrs['Impp'] = self.mppt.Impp
    if (self.mppt.Impp != None) and (self.mppt.Vmpp != None):
      self.f[self.position+'/'+self.pixel].attrs['ssPmax'] = abs(self.mppt.Impp * self.mppt.Vmpp)

  def mpptCB(measurement):
    """Callback function for max power point tracker
    (for live tracking)
    """
    [v, i, t, status] = measurement
    print('At {:.6f}\t{:.6f}\t{:.6f}\t{:d}'.format(t, v, i, int(status)))