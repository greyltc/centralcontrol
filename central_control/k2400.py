#!/usr/bin/env python

import sys
import time
import pyvisa as visa
import os

class k2400:
  """
  Intertace for Keithley 2400 sourcemeter
  """
  idnContains = 'KEITHLEY'
  quiet=False
  idn = ''

  def __init__(self, visa_lib='@py', scan=False, addressString=None, terminator='\r', serialBaud=57600, front=False, twoWire=False, quiet=False):
    self.quiet = quiet
    self.readyForAction = False
    self.rm = self._getResourceManager(visa_lib)

    if scan:
      print(self.rm.list_resources())

    self.addressString = addressString
    self.terminator = terminator
    self.serialBaud = serialBaud
    self.sm = self._getSourceMeter(self.rm)
    self._setupSourcemeter(front=front, twoWire=twoWire)

  def __del__(self):
    try:
      self.sm.close()
    except:
      pass

    try:
      g = self.sm.visalib.sessions[self.sm.session]
      g.close(g.interface.id)
    except:
      pass

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

    if not self.quiet:
      print("Using {:s} pyvisa backend.".format(self.backend))
    return rm

  def _getSourceMeter(self, rm):
    timeoutMS = 30000 # initial comms timeout, needs to be long for serial devices because things acan back up and they're slow
    if 'ASRL' in self.addressString:
      openParams = {'resource_name': self.addressString, 'timeout': timeoutMS, 'read_termination': self.terminator, 'write_termination': "\r", 'baud_rate': self.serialBaud, 'flow_control':visa.constants.VI_ASRL_FLOW_XON_XOFF, 'parity': visa.constants.Parity.none, 'allow_dma': True}
      smCommsMsg = "ERROR: Can't talk to sourcemeter\nDefault sourcemeter serial comms params are: 57600-8-n with <CR> terminator and xon-xoff flow control."
    elif 'GPIB' in self.addressString:
      openParams = {'resource_name': self.addressString, 'write_termination': self.terminator}# , 'io_protocol': visa.constants.VI_HS488
      addrParts = self.addressString.split('::')
      board = addrParts[0][4:]
      address = addrParts[1]
      smCommsMsg = "ERROR: Can't talk to sourcemeter\nIs GPIB controller {:} correct?\nIs the sourcemeter configured to listen on address {:}?".format(board,address)
    elif ('TCPIP' in self.addressString) and ('SOCKET' in self.addressString):
      addrParts = self.addressString.split('::')
      host = addrParts[1]
      port = host = addrParts[2]
      openParams = {'resource_name': self.addressString, 'timeout': timeoutMS, 'read_termination': "\n"}
      smCommsMsg = f"ERROR: Can't talk to sourcemeter\nTried Ethernet<-->Serial link via {host}:{port}\nThe sourcemeter's comms parameters must match the Ethernet<-->Serial adapter's parameters\nand the terminator should be configured as <CR>"
    else:
      smCommsMsg = "ERROR: Can't talk to sourcemeter"
      openParams = {'resource_name': self.addressString}

    sm = rm.open_resource(**openParams)

    if sm.interface_type == visa.constants.InterfaceType.gpib:
      if os.name != 'nt':
        sm.send_ifc()  # linux-gpib can do this. windows can't?
    
    sm.clear()  # clear the interface

    # discard all buffers
    sm.flush(visa.constants.VI_READ_BUF_DISCARD)
    sm.flush(visa.constants.VI_WRITE_BUF_DISCARD)
    sm.flush(visa.constants.VI_IO_IN_BUF_DISCARD)
    sm.flush(visa.constants.VI_IO_OUT_BUF_DISCARD)

    try:
      sm.write('*RST')
      sm.query('*OPC?')  # wait for the instrument to be ready
      sm.write('*CLS')
      sm.query('*OPC?')
      sm.write(':status:queue:clear') # clears error queue
      sm.query('*OPC?')
      sm.write(':system:preset')
      sm.query('*OPC?')
      # ask the device to identify its self
      self.idn = sm.query('*IDN?')
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
    else:
      raise ValueError("Got a bad response to *IDN?: {:s}".format(self.idn))

    return sm

  def _setupSourcemeter(self, twoWire, front):
    """ Do initial setup for sourcemeter
    """
    sm = self.sm
    sm.timeout = 50000  #long enough to collect an entire sweep [ms]
    self.auto_ohms = False

    sm.write(':status:preset')
    sm.query('*OPC?')
    sm.write(':trace:clear')
    sm.query('*OPC?')
    sm.write(':output:smode himpedance')
    sm.query('*OPC?')

    # binary transfer for GPIB
    if sm.interface_type == visa.constants.InterfaceType.gpib:
      sm.write("format:data {:s}".format('sreal'))

    sm.write('source:clear:auto off')
    sm.write('source:voltage:protection 20')  # the instrument will never generate over 20v

    self.setWires(twoWire=twoWire)

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

    sm.write(':system:azero off')  # we'll do this once before every measurement
    sm.write(':system:azero:caching on')

    # TODO: look into contact checking function of 2400 :system:ccheck

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

  def query_values(self, query):
    if self.sm.interface_type == visa.constants.InterfaceType.gpib:
      vals = self.sm.query_binary_values(query)
    else:
      vals = self.sm.query_ascii_values(query)
    if self.auto_ohms == False:
      m_len = 4
    else:
      m_len = 5
    realigned = list(zip(*[iter(vals)]*m_len))
    return realigned

  def outOn(self, on=True):
    if on:
      self.sm.write(':output on')
    else:
      self.sm.write(':output off')

  def setNPLC(self, nplc):
    self.sm.write(':sense:current:nplcycles {:}'.format(nplc))
    self.sm.write(':sense:voltage:nplcycles {:}'.format(nplc))
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
    auto_ohms = true will override everything and make the output data change to (time, resistance, current, status)
    """
    sm = self.sm
    if auto_ohms == True:
      sm.write(':sense:function:on "resistance"')
      sm.write(':sense:resistance:mode auto')
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
      sm.write(':source:function {:s}'.format(src))
      sm.write(':source:{:s}:mode fixed'.format(src))
      sm.write(':source:{:s} {:.8f}'.format(src,setPoint))

      sm.write(':source:delay:auto on')

      sm.write(':sense:function "{:s}"'.format(snc))
      sm.write(':sense:{:s}:protection {:.8f}'.format(snc,compliance))

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

    sm.write(':output on')
    sm.write(':trigger:count 1')

    sm.write(':system:azero once')
    self.opc() # ensure the instrument is ready after all this

  def setupSweep(self, sourceVoltage=True, compliance=0.04, nPoints=101, stepDelay=0.005, start=0, end=1, senseRange='f'):
    """setup for a sweep operation
    if senseRange == 'a' the instrument will auto range for both current and voltage measurements
    if senseRange == 'f' then the sense range will follow the compliance setting
    if stepDelay == -1 then step delay is on auto (1ms)
    """
    sm = self.sm
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

  def opc(self):
    """returns when all operations are complete
    """
    self.sm.query('*OPC?')
    return

  def arm(self):
    """arms trigger
    """
    self.sm.write(':init')

  def trigger(self):
    """performs trigger event
    """
    if self.sm.interface_type == visa.constants.InterfaceType.gpib:
      self.sm.assert_trigger()
    else:
      self.sm.write('*TRG')

  def sendBusCommand(self, command):
    """sends a command over the GPIB bus
    See: https://linux-gpib.sourceforge.io/doc_html/gpib-protocol.html#REFERENCE-COMMAND-BYTES
    """
    if self.sm.interface_type == visa.constants.InterfaceType.gpib:
      self.sm.send_command(command)
      #self.sm.send_command(0x08) # whole bus trigger
    else:
      print('Bus commands can only be sent over GPIB')

  def measure(self, nPoints=1):
    """Makes a measurement and returns the result
    returns a list of measurements
    a "measurement" is a tuple of length 4: voltage,current,time,statuss (or length 5: voltage,current,resistance,time,status if dc setup was done in ohms mode)
    for a prior DC setup, the list will be 1 long.
    for a prior sweep setup, the list returned will be n sweep points long
    """
    if self.sm.interface_type == visa.constants.InterfaceType.gpib:
      vals = self.sm.read_binary_values(data_points=nPoints*4)
    else:
      vals = self.query_values(':read?')
    if len(vals) > 1:
      print(f"Approx sweep duration = {vals[-1][0] - vals[0][0]} s")
    return vals

  def measureUntil(self, t_dwell=float('Infinity'), measurements=float('Infinity'), cb=lambda x:None):
    """Meakes a series of single dc measurements
    until termination conditions are met
    supports a callback after every measurement
    cb gets a measurement every time one is made
    returns a list of measurements
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

# testing code
if __name__ == "__main__":
  import pandas as pd
  import numpy as np

  # connect to our instrument and use the front terminals
  # for testing GPIB connections
  #k = k2400(addressString='GPIB0::24::INSTR') # gpib address strings expect the thing to be configured for 488.1 comms
  
  # for testing Ethernet <--> Serial adapter connections, in this case the adapter must be configured properly via its web interface
  #k = k2400(addressString='TCPIP0::10.45.0.186::4000::SOCKET', front=True)
  start = time.time()

  # for serial connection testing expects flow control to be on, data bits =8 and parity = none
  k = k2400(addressString='ASRL/dev/ttyS0::INSTR', terminator='\r', serialBaud=57600)

  print(f"Connected to {k.addressString}")

  # setup DC resistance measurement
  k.setupDC(auto_ohms=True)

  # this sets up the trigger/reading method we'll use below
  k.write(':arm:source immediate')
  
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
  sw_m = k.measure(nPoints = numPoints) # make the measurement
  tend = time.time()-t0

  # convert the result to a numpy array
  sw_ma = np.array(sw_m, dtype=measurement_datatype)

  # convert the result to a pandas dataframe and print it
  sw_mf = pd.DataFrame(sw_ma)
  print(f"===== {numPoints} point sweep from V={startV} to V={endV} in {tend} seconds =====")
  #print(dc_mf.to_string(formatters={'status':'{0:024b}'.format}))

  # shut off the output
  k.outOn(False)

  k.__del__() # TODO: switch to context manager for proper cleanup

  print(f"Total Time = {time.time()-start} seconds")
