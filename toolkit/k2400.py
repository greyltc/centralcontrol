import visa # for talking to sourcemeter
import pyvisa
import serial
import sys
import numpy as np

class k2400:
  """
  Intertace for Keithley 2400 sourcemeter
  """
  idnContains = 'KEITHLEY'
  
  def __init__(self, visa_lib='@py', scan=False, addressString=None, terminator='\n', serialBaud=57600, front=False, twoWire=False):
    self.readyForAction = False
    self.rm = self._getResourceManager(visa_lib)
    
    if scan:
      print(self.rm.list_resources())
    
    self.addressString = addressString
    self.terminator = terminator
    self.serialBaud = serialBaud     
    if addressString != None:
      self.sm = self._getSourceMeter(self.rm)
      self.readyForAction = self._setupSourcemeter(front=front, twoWire=twoWire)
    
  def _getResourceManager(self,visa_lib):
    try:
      rm = visa.ResourceManager(visa_lib)
    except:
      exctype, value1 = sys.exc_info()[:2]
      try:
        rm = visa.ResourceManager()
      except:
        exctype, value2 = sys.exc_info()[:2]
        print('Unable to connect to instrument.')
        print('Error 1 (using {:s} backend):'.format(visa_lib))
        print(value1)
        print('Error 2 (using pyvisa default backend):')
        print(value2)
        raise ValueError("Unable to create a resource manager.")
    
    vLibPath = rm.visalib.get_library_paths()[0]
    if vLibPath == 'unset':
      self.backend = 'pyvisa-py'
    else:
      self.backend = vLibPath
    
    print("Using {:s} pyvisa backend.".format(self.backend))
    return rm
    
  def _getSourceMeter(self, rm):
    timeoutMS = 300 # initial comms timeout
    if 'ASRL' in self.addressString:
      openParams = {'resource_name': self.addressString, 'timeout': timeoutMS, 'read_termination': self.terminator,'write_termination': self.terminator, 'baud_rate': self.serialBaud, 'flow_control':visa.constants.VI_ASRL_FLOW_XON_XOFF}
      smCommsMsg = "ERROR: Can't talk to sourcemeter\nDefault sourcemeter serial comms params are: 57600-8-n with <LF> terminator and xon-xoff flow control."
    elif 'GPIB' in self.addressString:
      openParams = {'resource_name': self.addressString, 'write_termination': self.terminator}# , 'io_protocol': visa.constants.VI_HS488
      addrParts = self.addressString.split('::')
      board = addrParts[0][4:]
      address = addrParts[1]
      smCommsMsg = "ERROR: Can't talk to sourcemeter\nIs GPIB controller {:} correct?\nIs the sourcemeter configured to listen on address {:}?".format(board,address)
    else:
      smCommsMsg = "ERROR: Can't talk to sourcemeter"
      openParams = {'resource_name': self.addressString}
    
    sm = rm.open_resource(**openParams)
    
    if sm.interface_type == visa.constants.InterfaceType.gpib:
      sm.send_ifc()
      sm.clear()
      sm._read_termination = '\n'
      
    try:
      sm.write('*RST')
      sm.write(':status:preset')
      sm.write(':system:preset')      
      # ask the device to identify its self
      idnString = sm.query('*IDN?')
    except:
      print('Unable perform "*IDN?" query.')
      exctype, value = sys.exc_info()[:2]
      print(value)
      try:
        sm.close()
      except:
        pass
      print(smCommsMsg)
      raise ValueError("Failed to talk to sourcemeter.")
    
    if self.idnContains in idnString:
      print("Sourcemeter found:")
      print(idnString)
    else:
      raise ValueError("Got a bad response to *IDN?: {:s}".format(idnString))
  
    return sm
  
  def _setupSourcemeter(self, twoWire, front):
    """ Do initial setup for sourcemeter
    """
    sm = self.sm
    sm.timeout = 50000 #long enough to collect an entire sweep
    
    sm.write(':status:preset')
    sm.write(':system:preset')
    sm.write(':trace:clear')
    sm.write(':output:smode himpedance')    
    
    if sm.interface_type == visa.constants.InterfaceType.asrl:
      self.dataFormat = 'ascii'
      sm.values_format.use_ascii('f',',')
    elif sm.interface_type == visa.constants.InterfaceType.gpib:
      self.dataFormat = 'sreal'
      sm.values_format.use_binary('f', False, container=np.array)
    else:
      self.dataFormat = 'ascii'
      sm.values_format.use_ascii('f',',')
      
    sm.write("format:data {:s}".format(self.dataFormat))
    
    sm.write('source:clear:auto off')
    sm.write(':system:azero on')
    
    if twoWire:
      sm.write(':system:rsense off') # four wire mode off
    else:
      sm.write(':system:rsense on') # four wire mode on
      
    sm.write(':sense:function:concurrent on')
    sm.write(':sense:function "current:dc", "voltage:dc"')
    sm.write(':format:elements time,voltage,current,status')
    
    # use front terminals?
    if front:
      sm.write(':rout:term front')
    else:
      sm.write(':rout:term rear')
      
    self.src = sm.query(':source:function:mode?')
      
    return True
  
  def disconnect(self):
    sm = self.sm
    sm.write('*RST')
    sm.close()
  
    print("Sourcemeter disconnected.")
    
  def updateSweepStart(self,startVal):
    self.sm.write(':source:{:s}:start {:.6f}'.format(self.src, startVal))

    
  def updateSweepStop(self,stopVal):
    self.sm.write(':source:{:s}:stop {:.6f}'.format(self.src, stopVal))

  def setOutput(self, outVal):
    self.sm.write(':source:{:s} {:.6f}'.format(self.src,outVal))
    
  def write(self, toWrite):
    self.sm.write(toWrite)
    
  def query_values(self, query):
    if self.dataFormat == 'ascii':
      return self.sm.query_ascii_values(query)
    elif self.dataFormat == 'sreal':
      return self.sm.query_binary_values(query)
    else:
      raise ValueError("Don't know what values format to use!")
    
  def outOn(self, on=True):
    if on:
      self.sm.write(':output on')
    else:
      self.sm.write(':output off')      
    
  def setupSweep(self,sweepParams):
    sm = self.sm
    if sweepParams['voltage']:
        src = 'voltage'
        snc = 'current'
    else:
        src = 'current'
        snc = 'voltage'
    self.src = src
    sm.write(':source:{:s} {:0.4f}'.format(src,sweepParams['sweepStart']))
    sm.write(':source:function {:s}'.format(src))
    sm.write(':output on')
    sm.write(':source:{:s}:mode sweep'.format(src))
    sm.write(':source:sweep:spacing linear')
    if sweepParams['stepDelay'] == -1:
        sm.write(':source:delay:auto on') # this just sets delay to 1ms
    else:
        sm.write(':source:delay:auto off')
        sm.write(':source:delay {:0.3f}'.format(sweepParams['stepDelay']))
    sm.write(':trigger:count {:d}'.format(int(sweepParams['nPoints'])))
    sm.write(':source:sweep:points {:d}'.format(int(sweepParams['nPoints'])))
    sm.write(':source:{:s}:start {:.4f}'.format(src,sweepParams['sweepStart']))
    sm.write(':source:{:s}:stop {:.4f}'.format(src,sweepParams['sweepEnd']))
    #sm.write(':source:{:s}:range {:.4f}'.format(src,max(sweepParams['sweepStart'],sweepParams['sweepStart'])))
    sm.write(':source:sweep:ranging best')
    sm.write(':sense:{:s}:protection {:.6f}'.format(snc,sweepParams['compliance']))
    sm.write(':sense:{:s}:range {:.6f}'.format(snc,sweepParams['compliance']))
    sm.write(':sense:current:nplcycles {:}'.format(sweepParams['nplc']))
    sm.write(':sense:voltage:nplcycles {:}'.format(sweepParams['nplc']))
    sm.write(':display:digits 5')    
    