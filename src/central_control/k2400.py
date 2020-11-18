#!/usr/bin/env python

import sys
import time
import pyvisa

class k2400:
  """
  Intertace for Keithley 2400 sourcemeter
  """
  idnContains = 'KEITHLEY'
  quiet=False
  idn = ''
  status = 0
  nplc_user_set = 1.0
  last_sweep_time = 0

  def __init__(self, visa_lib='@py', scan=False, addressString=None, terminator='\r', serialBaud=57600, front=False, twoWire=False, quiet=False):
    self.quiet = quiet
    self.readyForAction = False
    self.four88point1 = False
    self.rm = self._getResourceManager(visa_lib)

    if scan:
      print(self.rm.list_resources())

    self.addressString = addressString
    self.terminator = terminator
    self.serialBaud = serialBaud
    self.sm, self.ifc = self._getSourceMeter(self.rm)
    self._setupSourcemeter(front=front, twoWire=twoWire)

  def __del__(self):
    try:
      self.sm.write('abort')
    except:
      pass

    try:
      self.sm.clear()  # SDC (selective device clear) signal
    except:
      pass

    try:
      self.sm.write(':output off')
    except:
      pass

    # attempt to get into local mode
    try:
      self.opc()
      self.sm.write(':system:local')
      time.sleep(0.2) # wait 200ms for this command to execute before closing the interface
      if (not self.four88point1) and (self.sm.interface_type != pyvisa.constants.InterfaceType.asrl):
        # this doesn't work in 488.1 mode
        self.sm.visalib.sessions[self.sm.session].interface.ibloc()
    except:
      pass

    try:
      self.sm.close()
    except:
      pass

    try:
      self.ifc.close()
    except:
      pass

    # then just close everything possible
    try:
      for num, ses in self.rm.visalib.sessions.items():
        try:
          ses.close()
        except:
          pass
    except:
      pass


  def _getResourceManager(self,visa_lib):
    try:
      rm = pyvisa.ResourceManager(visa_lib)
    except:
      exctype, value1 = sys.exc_info()[:2]
      try:
        rm = pyvisa.ResourceManager()
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

    if not self.quiet:
      print("Using {:s} pyvisa backend.".format(self.backend))
    return rm

  def _getSourceMeter(self, rm):
    timeoutMS = 30000 # initial comms timeout, needs to be long for serial devices because things acan back up and they're slow
    open_params = {}
    open_params['resource_name'] = self.addressString

    if 'ASRL' in self.addressString:
      open_params['timeout'] = timeoutMS
      open_params['write_termination'] = self.terminator
      open_params['read_termination'] = self.terminator
      open_params['baud_rate'] = self.serialBaud

      # this likely does nothing (I think these hardware flow control lines go nowhere useful inside the 2400)
      open_params['flow_control'] = pyvisa.constants.VI_ASRL_FLOW_RTS_CTS
      
      # this seems to be very bad. known to lock up usb<-->serial bridge hardware
      #open_params['flow_control'] = pyvisa.constants.VI_ASRL_FLOW_XON_XOFF

      open_params['parity'] = pyvisa.constants.Parity.none
      #open_params['allow_dma'] = True

      smCommsMsg = "ERROR: Can't talk to sourcemeter\nDefault sourcemeter serial comms params are: 57600-8-n with <CR> terminator and NONE flow control."
    elif 'GPIB' in self.addressString:
      open_params['write_termination'] = "\n"
      open_params['read_termination'] = "\n"
      #open_params['io_protocol'] = pyvisa.constants.VI_HS488
      
      addrParts = self.addressString.split('::')
      controller = addrParts[0]
      board = controller[4:]
      address = addrParts[1]
      smCommsMsg = f"ERROR: Can't talk to sourcemeter\nIs GPIB controller {board} correct?\nIs the sourcemeter configured to listen on address {address}? Try both SCPI and 488.1 comms modes, though 488.1 should be much faster"
    elif ('TCPIP' in self.addressString) and ('SOCKET' in self.addressString):
      open_params['timeout'] = timeoutMS
      open_params['write_termination'] = "\n"
      open_params['read_termination'] = "\n"

      addrParts = self.addressString.split('::')
      host = addrParts[1]
      port = host = addrParts[2]
      smCommsMsg = f"ERROR: Can't talk to sourcemeter\nTried Ethernet<-->Serial link via {host}:{port}\nThe sourcemeter's comms parameters must match the Ethernet<-->Serial adapter's parameters\nand the terminator should be configured as <CR>"
    else:
      smCommsMsg = "ERROR: Can't talk to sourcemeter"
      open_params = {'resource_name': self.addressString}

    sm = rm.open_resource(**open_params)
    
    # attempt to make a GPIB interface object and ensure it's enabled for remote control
    try:
      ifc = rm.open_resource(f'{controller}::INTFC')
      ifc.visalib.sessions[ifc.session].controller.remote_enable(1)
    except:
      ifc = None

    # attempt to send SDC (selective device clear) signal
    try:
      sm.clear()
    except:
      pass

    if sm.interface_type == pyvisa.constants.InterfaceType.asrl:
      # discard all buffers
      sm.flush(pyvisa.constants.VI_READ_BUF_DISCARD)
      sm.flush(pyvisa.constants.VI_WRITE_BUF_DISCARD)
      sm.flush(pyvisa.constants.VI_IO_IN_BUF_DISCARD)
      sm.flush(pyvisa.constants.VI_IO_OUT_BUF_DISCARD)

    try:
      sm.write('*RST')
    except:
      pass

    self.check488point1(sm=sm)
    if not self.four88point1:
      try:  # do a bunch of stuff to attempt to get in sync with apossibly misbehaving instrument
        self.opc(sm=sm) # wait for the instrument to be ready
        sm.write('*CLS')
        self.opc(sm=sm)
        sm.write(':status:queue:clear') # clears error queue
        self.opc(sm=sm)
        sm.write(':system:preset')
        self.opc(sm=sm)
        self.opc(sm=sm)
        # if there is garbage left in the input buffer toss it
        time.sleep(0.1)
        #session = sm.visalib.sessions[sm._session]  # that's a pyserial object
        #session.interface.reset_input_buffer()
        if hasattr(sm, 'bytes_in_buffer'):
          if sm.bytes_in_buffer > 0:
            sm.read_raw(sm.bytes_in_buffer)
      except:
        pass

    try:
      self.idn = sm.query('*IDN?')  # ask the device to identify its self
    except:
      print('Unable perform "*IDN?" query.')
      exctype, value = sys.exc_info()[:2]
      print(value)
      #try:
      #  sm.close()
      #except:
      #  pass
      print(smCommsMsg)
      raise ValueError("Failed to talk to sourcemeter.")

    if self.idnContains in self.idn:
      if not self.quiet:
        print("Sourcemeter found:")
        print(self.idn)
      if not self.four88point1:
        self.check488point1(sm=sm)
    else:
      raise ValueError("Got a bad response to *IDN?: {:s}".format(self.idn))

    return sm, ifc
  
  # attempt to learn if the machine is in 488.1 mode (fast comms)
  def check488point1(self, sm=None):
    if sm == None:
      sm = self.sm
    try:
      if (sm.interface_type == pyvisa.constants.InterfaceType.gpib) and (sm.query(':system:mep:state?') == '0'):
        self.four88point1 = True
        print('High performance 488.1 comms mode activated!')
      else:
        self.four88point1 = False
    except:
      self.four88point1 = False

  def _setupSourcemeter(self, twoWire, front):
    """ Do initial setup for sourcemeter
    """
    sm = self.sm
    sm.timeout = 50000  #long enough to collect an entire sweep [ms]
    self.auto_ohms = False

    sm.write(':status:preset')
    self.opc()
    sm.write(':trace:clear')
    self.opc()
    sm.write(':output:smode himpedance')
    self.opc()

    # set data transfer type
    if sm.interface_type == pyvisa.constants.InterfaceType.asrl:
      sm.write("format:data {:s}".format('ascii'))
    else:
      sm.write("format:data {:s}".format('sreal'))
    
    sm.write('source:clear:auto off')
    sm.write('source:voltage:protection 20')  # the instrument will never generate over 20v

    self.setWires(twoWire=twoWire)
    self.opc()
    sm.write(':sense:function:concurrent on')
    sm.write(':sense:function "current:dc", "voltage:dc"')
    sm.write(':format:elements time,voltage,current,status')
    # status is a 24 bit intiger. bits are these:
    # Bit 0 (OFLO) — Set to 1 if measurement was made while in over-range.
    # Bit 1 (Filter) — Set to 1 if measurement was made with the filter enabled.
    # Bit 2 (Front/Rear) — Set to 1 if FRONT terminals are selected.
    # Bit 3 (Compliance) — Set to 1 if in real compliance.
    # Bit 4 (OVP) — Set to 1 if the over voltage protection limit was reached.
    # Bit 5 (Math) — Set to 1 if math expression (calc1) is enabled.
    # Bit 6 (Null) — Set to 1 if Null is enabled.
    # Bit 7 (Limits) — Set to 1 if a limit test (calc2) is enabled.
    # Bits 8 and 9 (Limit Results) — Provides limit test results (see grading  and sorting modes below).
    # Bit 10 (Auto-ohms) — Set to 1 if auto-ohms enabled.
    # Bit 11 (V-Meas) — Set to 1 if V-Measure is enabled.
    # Bit 12 (I-Meas) — Set to 1 if I-Measure is enabled.
    # Bit 13 (Ω-Meas) — Set to 1 if Ω-Measure is enabled.
    # Bit 14 (V-Sour) — Set to 1 if V-Source used.Bit 15 (I-Sour) — Set to 1 if I-Source used.
    # Bit 16 (Range Compliance) — Set to 1 if in range compliance.
    # Bit 17 (Offset Compensation) — Set to 1 if Offset Compensated Ohms is     enabled.
    # Bit 18 — Contact check failure (see AppendixF).
    # Bits 19, 20 and 21 (Limit Results) — Provides limit test results (see grading and sorting modes below).
    # Bit 22 (Remote Sense) — Set to 1 if 4-wire remote sense selected.
    # Bit 23 (Pulse Mode) — Set to 1 if in the Pulse Mode.

    # use front terminals?
    self.setTerminals(front=front)

    self.src = sm.query(':source:function:mode?')
    sm.write(':system:beeper:state off')
    sm.write(':system:lfrequency:auto on')
    sm.write(':system:time:reset')
    self.opc()

    sm.write(':system:azero off')  # we'll do this once before every measurement
    sm.write(':system:azero:caching on')
    self.opc()

    # enable/setup contact check :system:ccheck
    opts = self.sm.query("*opt?")
    if "CONTACT-CHECK" in opts.upper():
      sm.write(':system:ccheck off')
      sm.write(':system:ccheck:resistance 50')  # choices are 2, 15 or 50

  # note that this also checks the GUARD-SENSE connections, short those manually if not in use
  def set_ccheck_mode(self, value=True):
    if self.sm.query(':system:rsense?') == "1":
      opts = self.sm.query("*opt?")
      if "CONTACT-CHECK" in opts.upper():
        if value == True:
          self.outOn(on=False)
          self.sm.write(':output:smode guard')
          self.sm.write(':system:ccheck on')
          self.opc()
          self.sm.write(':sense:voltage:nplcycles 0.1')
          # setup I=0 voltage measurement
          self.setupDC(sourceVoltage=False, compliance=3, setPoint=0, senseRange='f', auto_ohms=False)
          self.sm.write(':arm:source immediate')
        else:
          self.sm.write(':output:smode himpedance')
          self.outOn(on=False)
          self.opc()
          self.sm.write(f':sense:voltage:nplcycles {self.nplc_user_set}')
          self.sm.write(':system:ccheck off')
      else:
        print("Contact check option not installed")
    else:
      print("Contact check function requires 4-wire mode")

  def disconnect(self):
    self.__del__()

  def setWires(self, twoWire=False):
    if twoWire:
      self.sm.write(':system:rsense off') # four wire mode off
    else:
      self.sm.write(':system:rsense on') # four wire mode on

  def setTerminals(self, front=False):
    if front:
      self.sm.write(':rout:term front')
    else:
      self.sm.write(':rout:term rear')

  def updateSweepStart(self,startVal):
    self.sm.write(':source:{:s}:start {:.8f}'.format(self.src, startVal))

  def updateSweepStop(self,stopVal):
    self.sm.write(':source:{:s}:stop {:.8f}'.format(self.src, stopVal))

  # sets the source to some value
  def setSource(self, outVal):
    self.sm.write(':source:{:s} {:.8f}'.format(self.src,outVal))

  def write(self, toWrite):
    self.sm.write(toWrite)

  def outOn(self, on=True):
    if on:
      self.sm.write(':output on')
    else:
      self.sm.write(':output off')

  def setNPLC(self, nplc):
    self.nplc_user_set = nplc
    self.sm.write(':sense:current:nplcycles {:}'.format(nplc))
    self.sm.write(':sense:voltage:nplcycles {:}'.format(nplc))
    self.opc()
    self.sm.write(':sense:resistance:nplcycles {:}'.format(nplc))
    if nplc < 1:
      self.sm.write(':display:digits 5')
    else:
      self.sm.write(':display:digits 7')

  def setupDC(self, sourceVoltage=True, compliance=0.04, setPoint=0, senseRange='f', auto_ohms = False):
    """setup DC measurement operation
    if senseRange == 'a' the instrument will auto range for both current and voltage measurements
    if senseRange == 'f' then the sense range will follow the compliance setting
    if sourceVoltage == False, we'll have a current source at setPoint amps with max voltage +/- compliance volts
    auto_ohms = true will override everything and make the output data change to (voltage,current,resistance,time,status)
    """
    sm = self.sm
    self.opc()
    if auto_ohms == True:
      sm.write(':sense:function:on "resistance"')
      sm.write(':sense:resistance:mode auto')
      self.opc()
      sm.write(':sense:resistance:range:auto on')
      #sm.write(':sense:resistance:range 20E3')
      sm.write(':format:elements voltage,current,resistance,time,status')
      self.auto_ohms = True
    else:
      self.auto_ohms = False
      sm.write(':sense:function:off "resistance"')
      if sourceVoltage:
        src = 'voltage'
        snc = 'current'
      else:
        src = 'current'
        snc = 'voltage'
      self.src = src
      self.opc()
      sm.write(':source:function {:s}'.format(src))
      sm.write(':source:{:s}:mode fixed'.format(src))
      sm.write(':source:{:s} {:.8f}'.format(src,setPoint))

      sm.write(':source:delay:auto on')

      sm.write(':sense:function "{:s}"'.format(snc))
      sm.write(':sense:{:s}:protection {:.8f}'.format(snc,compliance))

      self.opc()
      if senseRange == 'f':
        sm.write(':sense:{:s}:range:auto off'.format(snc))
        sm.write(':sense:{:s}:protection:rsynchronize on'.format(snc))
      elif senseRange == 'a':
        sm.write(':sense:{:s}:range:auto on'.format(snc))
      else:
        sm.write(':sense:{:s}:range {:.8f}'.format(snc,senseRange))

      # this again is to make sure the sense range gets updated
      sm.write(':sense:{:s}:protection {:.8f}'.format(snc,compliance))
      sm.write(':format:elements voltage,current,time,status')
    self.opc()
    sm.write(':output on')
    sm.write(':trigger:count 1')

    sm.write(':system:azero once')
    self.opc() # ensure the instrument is ready after all this

  def setupSweep(self, sourceVoltage=True, compliance=0.04, nPoints=101, stepDelay=-1, start=0, end=1, senseRange='f'):
    """setup for a sweep operation
    if senseRange == 'a' the instrument will auto range for both current and voltage measurements
    if senseRange == 'f' then the sense range will follow the compliance setting
    if stepDelay == -1 then step delay is on auto (1ms)
    """
    sm = self.sm
    self.opc()
    if sourceVoltage:
      src = 'voltage'
      snc = 'current'
    else:
      src = 'current'
      snc = 'voltage'
    self.src = src
    sm.write(':source:function {:s}'.format(src))
    sm.write(':source:{:s} {:0.6f}'.format(src,start))

    # seems to do exactly nothing
    #if snc == 'current':
    #  holdoff_delay = 0.005
    #  sm.write(':sense:current:range:holdoff on')
    #  sm.write(':sense:current:range:holdoff {:.6f}'.format(holdoff_delay))
    #  self.opc()  # needed to prevent input buffer overrun with serial comms (should be taken care of by flowcontrol!)

    sm.write(':sense:{:s}:protection {:.8f}'.format(snc,compliance))

    if senseRange == 'f':
      sm.write(':sense:{:s}:range:auto off'.format(snc))
      sm.write(':sense:{:s}:protection:rsynchronize on'.format(snc))
    elif senseRange == 'a':
      sm.write(':sense:{:s}:range:auto on'.format(snc))
    else:
      sm.write(':sense:{:s}:range {:.8f}'.format(snc,senseRange))

    self.opc()
    # this again is to make sure the sense range gets updated
    sm.write(':sense:{:s}:protection {:.8f}'.format(snc,compliance))

    sm.write(':output on')
    sm.write(':source:{:s}:mode sweep'.format(src))
    sm.write(':source:sweep:spacing linear')
    if stepDelay == -1:
      sm.write(':source:delay:auto on') # this just sets delay to 1ms
    else:
      sm.write(':source:delay:auto off')
      sm.write(':source:delay {:0.6f}'.format(stepDelay))
    self.opc()
    sm.write(':trigger:count {:d}'.format(nPoints))
    sm.write(':source:sweep:points {:d}'.format(nPoints))
    sm.write(':source:{:s}:start {:.6f}'.format(src,start))
    sm.write(':source:{:s}:stop {:.6f}'.format(src,end))
    if sourceVoltage:
      self.dV = abs(float(sm.query(':source:voltage:step?')))
    else:
      self.dI = abs(float(sm.query(':source:current:step?')))
    #sm.write(':source:{:s}:range {:.4f}'.format(src,max(start,end)))
    sm.write(':source:sweep:ranging best')
    #sm.write(':sense:{:s}:range:auto off'.format(snc))

    sm.write(':system:azero once')
    self.opc() # ensure the instrument is ready after all this

  def opc(self, sm=None):
    """returns when all operations are complete
    """
    if not self.four88point1:
      if sm == None:
        sm = self.sm
      sm.write('*WAI')
      one = sm.query('*OPC?')
      if one != '1':
        print(f"OPC returned {one}")

  def arm(self):
    """arms trigger
    """
    self.sm.write(':init')

  def trigger(self):
    """performs trigger event
    """
    if self.sm.interface_type == pyvisa.constants.InterfaceType.gpib:
      self.sm.assert_trigger()
    else:
      self.sm.write('*TRG')

  def sendBusCommand(self, command):
    """sends a command over the GPIB bus
    See: https://linux-gpib.sourceforge.io/doc_html/gpib-protocol.html#REFERENCE-COMMAND-BYTES
    """
    if self.sm.interface_type == pyvisa.constants.InterfaceType.gpib:
      self.sm.send_command(command)
      #self.sm.send_command(0x08) # whole bus trigger
    else:
      print('Bus commands can only be sent over GPIB')

  def measure(self, nPoints=1):
    """Makes a measurement and returns the result
    returns a list of measurements
    a "measurement" is a tuple of length 4: voltage,current,time,status (or length 5: voltage,current,resistance,time,status if dc setup was done in ohms mode)
    for a prior DC setup, the list will be 1 long.
    for a prior sweep setup, the list returned will be n sweep points long
    """
    # auto ohms measurements return length 5 data points
    if self.auto_ohms == False:
      m_len = 4
    else:
      m_len = 5

    if self.sm.interface_type == pyvisa.constants.InterfaceType.asrl:
      # ascii data only for serial comms
      vals = self.sm.query_ascii_values(':read?')
    else:
      if self.four88point1 == True:
        # this only works in 488.1 because it can use the read address line to initiate the measurement
        vals = self.sm.read_binary_values(data_points=nPoints*m_len, is_big_endian=True)
      else:
        vals = self.sm.query_binary_values(':read?', data_points=nPoints*m_len)

    # turn this into a list of tuples because pretty much everything upstream likes that format
    reshaped = list(zip(*[iter(vals)]*m_len))

    # if this was a sweep, compute how long it took
    if len(reshaped) > 1:
      first_element = reshaped[0]
      last_element = reshaped[-1]
      if m_len == 4:
        t_start = first_element[2]
        t_end = last_element[2]
      elif m_len == 5:
        t_start = first_element[3]
        t_end = last_element[3]
      self.last_sweep_time = t_end - t_start
      print(f"Sweep duration = {self.last_sweep_time} s")
    
    # update the status byte
    self.status = int(reshaped[-1][-1])
    return reshaped

  def measureUntil(self, t_dwell=float('Infinity'), measurements=float('Infinity'), cb=lambda x:None):
    """Makes a series of single dc measurements
    until termination conditions are met
    supports a callback after every measurement
    cb gets a single tuple every time one is generated
    returns data in the same format as the measure command does:
    a list of tuples, where each element has length 4 normally, 5 for resistance
    """
    i = 0
    t_end = time.time() + t_dwell
    q = []
    self.opc() # before we start reading, ensure the device is ready
    while (i < measurements) and (time.time() < t_end):
      i = i + 1
      measurement = self.measure()[0]
      q.append(measurement)
      cb(measurement)
    return q
  
  def contact_check(self):
    """
    call set_ccheck_mode(True) before calling this
    and set_ccheck_mode(False) after you're done checking contacts
    attempts to turn on the output and trigger a measurement.
    tests if the output remains on after that. if so, the contact check passed
    True for contacted. always true if the sourcemeter hardware does not support this feature
    """
    good_contact = False
    self.sm.write(':output on')  # try to turn on the output
    if self.sm.query(':output?') == "1":  # check if that worked
      self.sm.write("INIT")
      time.sleep(0.1)  # TODO: figure out a better way to do this. mysterious dealys = bad
      if self.sm.query(':output?') == "1":
        good_contact = True  # if INIT didn't trip the output off, then we're connected
    return (good_contact)

# testing code
if __name__ == "__main__":
  import pandas as pd
  import numpy as np
  start = time.time()

  #address = "GPIB0::24::INSTR"
  #address = 'TCPIP0::10.45.0.186::4000::SOCKET'
  #address = 'ASRL/dev/ttyS0::INSTR'
  address = 'ASRL/dev/ttyUSB0::INSTR'

  k = k2400(addressString=address, terminator='\r', serialBaud=57600)

  con_time = time.time()
  print(f"Connected to {k.addressString} in {time.time()-con_time} seconds")

  # do a contact check
  k.set_ccheck_mode(True)
  print(f"Contact check result: {k.contact_check()}")
  k.set_ccheck_mode(False)

  # setup DC resistance measurement
  k.setupDC(auto_ohms=True)

  # this sets up the trigger/reading method we'll use below
  k.write(':arm:source immediate')

  print(f"One auto ohms measurement: {k.measure()}")

  # measure 
  mTime = 10
  k.setNPLC(1)
  dc_m = k.measureUntil(t_dwell=mTime)

  # create a custom data type to hold our data
  measurement_datatype = np.dtype({'names': ['voltage','current','resistance','time','status'], 'formats': ['f', 'f', 'f', 'f', 'u4'], 'titles': ['Voltage [V]', 'Current [A]', 'Resistance [Ohm]', 'Time [s]', 'Status bitmask']})
  
  # convert the data to a numpy array
  dc_ma = np.array(dc_m, dtype=measurement_datatype)

  # convert the data to a pandas dataframe and print it
  dc_mf = pd.DataFrame(dc_ma)
  print(f"===== {len(dc_mf)} auto ohms values in {mTime} seconds =====")
  #print(dc_mf.to_string(formatters={'status':'{0:024b}'.format}))

  # setup DC current measurement at 0V measurement
  forceV = 0
  k.setupDC(setPoint=forceV)

  print(f"One V=0 measurement: {k.measure()}")

  # this sets up the trigger/reading method we'll use below
  k.write(':arm:source immediate')
  
  # measure 
  mTime = 10
  k.setNPLC(0.01)
  dc_m = k.measureUntil(t_dwell=mTime)

  # create a custom data type to hold our data
  measurement_datatype = np.dtype({'names': ['voltage','current','time','status'], 'formats': ['f', 'f', 'f', 'u4'], 'titles': ['Voltage [V]', 'Current [A]', 'Time [s]', 'Status bitmask']})
  
  # convert the data to a numpy array
  dc_ma = np.array(dc_m, dtype=measurement_datatype)

  # convert the data to a pandas dataframe and print it
  dc_mf = pd.DataFrame(dc_ma)
  print(f"===== {len(dc_mf)} DC V={forceV} values in {mTime} seconds =====")
  #print(dc_mf.to_string(formatters={'status':'{0:024b}'.format}))

  # now for a 101 point voltage sweep from 0 --> 1V
  numPoints = 101
  startV = 0
  endV = 1
  k.setupSweep(compliance=0.01, nPoints=numPoints, start=startV, end=endV)  # set the sweep up
  t0 = time.time()
  # TODO: need to understand why the actual sweep here when done in serial mode is so much slower (not the comms)
  sw_m = k.measure(nPoints = numPoints) # make the measurement
  tend = time.time()-t0

  # convert the result to a numpy array
  sw_ma = np.array(sw_m, dtype=measurement_datatype)

  # convert the result to a pandas dataframe and print it
  sw_mf = pd.DataFrame(sw_ma)
  print(f"===== {len(sw_ma)} point sweep event from V={startV} to V={endV} completed in {tend:.2f}s ({k.last_sweep_time:.2f}s sweeping and {tend-k.last_sweep_time:2f}s data transfer) =====")
  #print(dc_mf.to_string(formatters={'status':'{0:024b}'.format}))

  # shut off the output
  k.outOn(False)

  k.__del__() # TODO: switch to context manager for proper cleanup

  print(f"Total Time = {time.time()-start} seconds")
