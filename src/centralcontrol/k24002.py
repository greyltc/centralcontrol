#!/usr/bin/env python

import sys
import time
import serial
import threading

try:
    from centralcontrol.logstuff import get_logger as getLogger
except:
    from logging import getLogger


class k2400(object):
    """
    Intertace for Keithley 2400 sourcemeter
    """

    idnContains = "KEITHLEY"
    quiet = False
    idn = ""
    status = 0
    nplc_user_set = 1.0
    last_sweep_time = 0
    readyForAction = False
    four88point1 = False
    default_comms_timeout = 50000  # in ms
    print_sweep_deets: bool = False  # false uses debug logging level, true logs sweep stats at info level

    def __init__(self, visa_lib="@py", scan=False, address: str = None, terminator="\r", serial_baud=57600, front=False, two_wire=False, quiet=False, killer=threading.Event(), print_sweep_deets=False, **kwargs):
        """just set class variables here"""

        self.lg = getLogger(".".join([__name__, type(self).__name__]))  # setup logging

        self.killer = killer
        self.quiet = quiet
        self.address = address
        self.terminator = terminator
        self.serial_baud = serial_baud
        self.front = front
        self.two_wire = two_wire
        self.scan = scan
        self.print_sweep_deets = print_sweep_deets

        self.lg.debug("k2400 initialized.")

    def connect(self):
        """attempt to connect to hardware and initialize it"""

        class HWURL(object):
            class Serial(serial.Serial):
                @serial.Serial.port.setter
                def port(self, value):
                    """translate port name before storing it"""
                    if isinstance(value, str) and value.startswith("hw://"):
                        try:
                            url_meat = value.removeprefix("hw://")
                            portsplit = url_meat.split("?")
                            serial.Serial.port.__set__(self, portsplit.pop(0))
                            if len(portsplit) != 0:
                                argsplit = portsplit[0].split("&")
                                for arg in argsplit:
                                    if "=" in arg:
                                        [argname, argval] = arg.split("=", 1)
                                        attr = getattr(serial.Serial, argname)
                                        if argname == "baudrate":
                                            argval = int(argval)
                                        elif argname in ("bytesize", "parity", "stopbits"):
                                            argval = getattr(serial, argval.upper())
                                        elif "timeout" in argname:
                                            if argval.lower() == "none":
                                                argval = None
                                            else:
                                                argval = float(argval)
                                        elif argname in ("xonxoff", "rtscts", "dsrdtr"):
                                            argval = argval.lower() in ("yes", "true", "t", "1")
                                        attr.__set__(self, argval)
                        except Exception as e:
                            raise ValueError(f"Failed parsing hw:// url: {e}")
                    elif value is None:
                        serial.Serial.port.__set__(self, value)
                    else:
                        raise ValueError(f"Expected hw:// url, got: {value}")

        sys.modules["hwurl"] = HWURL
        sys.modules["hwurl.protocol_hw"] = HWURL
        serial.protocol_handler_packages.append("hwurl")

        try:
            self.ser = serial.serial_for_url(self.address)
        except Exception as e:
            raise ValueError(f"Failure connecting to {self.address} with: {e}")

        self._setupSourcemeter(front=self.front, two_wire=self.two_wire)

        self.lg.debug("k2400 connected.")

        return 0

    def disconnect(self):
        try:
            self.sm.write(":abort")
        except:
            pass

        try:
            self.sm.clear()  # SDC (selective device clear) signal
        except:
            pass

        try:
            self.sm.write(":output off")
        except:
            pass

        # attempt to get into local mode
        try:
            self.opc()
            self.sm.write(":system:local")
            time.sleep(0.2)  # wait 200ms for this command to execute before closing the interface
            if (not self.four88point1) and (self.sm.interface_type != pyvisa.constants.InterfaceType.asrl):
                # this doesn't work in 488.1 mode
                self.sm.visalib.sessions[self.sm.session].interface.ibloc()
        except:
            pass

        try:
            self.sm.close()
        except:
            pass

    def _getSourceMeter(self, rm):
        timeoutMS = self.default_comms_timeout  # initial comms timeout, needs to be long for serial devices because things can back up and they're slow
        open_params = {}
        open_params["resource_name"] = self.address

        if "ASRL" in self.address:
            open_params["timeout"] = timeoutMS
            open_params["write_termination"] = self.terminator
            open_params["read_termination"] = self.terminator
            open_params["baud_rate"] = self.serial_baud

            # this likely does nothing (I think these hardware flow control lines go nowhere useful inside the 2400)
            open_params["flow_control"] = pyvisa.constants.VI_ASRL_FLOW_RTS_CTS

            # this seems to be very bad. known to lock up usb<-->serial bridge hardware
            # open_params['flow_control'] = pyvisa.constants.VI_ASRL_FLOW_XON_XOFF

            open_params["parity"] = pyvisa.constants.Parity.none
            # open_params['allow_dma'] = True

            smCommsMsg = "ERROR: Can't talk to sourcemeter\nDefault sourcemeter serial comms params are: 57600-8-n with <CR> terminator and NONE flow control."
        elif "GPIB" in self.address:
            open_params["write_termination"] = "\n"
            open_params["read_termination"] = "\n"
            # open_params['io_protocol'] = pyvisa.constants.VI_HS488

            addrParts = self.address.split("::")
            controller = addrParts[0]
            board = controller[4:]
            address = addrParts[1]
            smCommsMsg = f"ERROR: Can't talk to sourcemeter\nIs GPIB controller {board} correct?\nIs the sourcemeter configured to listen on address {address}? Try both SCPI and 488.1 comms modes, though 488.1 should be much faster"
        elif ("TCPIP" in self.address) and ("SOCKET" in self.address):
            open_params["timeout"] = timeoutMS
            open_params["write_termination"] = "\n"
            open_params["read_termination"] = "\n"

            addrParts = self.address.split("::")
            host = addrParts[1]
            port = host = addrParts[2]
            smCommsMsg = f"ERROR: Can't talk to sourcemeter\nTried Ethernet<-->Serial link via {host}:{port}\nThe sourcemeter's comms parameters must match the Ethernet<-->Serial adapter's parameters\nand the terminator should be configured as <CR>"
        else:
            smCommsMsg = "ERROR: Can't talk to sourcemeter"
            open_params = {"resource_name": self.address}

        sm = rm.open_resource(**open_params)

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
            sm.write(":abort")
        except:
            pass

        try:
            sm.write("*RST")
        except:
            pass

        self.check488point1(sm=sm)
        if not self.four88point1:
            try:  # do a bunch of stuff to attempt to get in sync with apossibly misbehaving instrument
                self.opc(sm=sm)  # wait for the instrument to be ready
                sm.write("*CLS")
                self.opc(sm=sm)
                sm.write(":status:queue:clear")  # clears error queue
                self.opc(sm=sm)
                sm.write(":system:preset")
                self.opc(sm=sm)
                self.opc(sm=sm)
            except:
                pass

        try:
            self.idn = sm.query("*IDN?")  # ask the device to identify its self
        except:
            self.lg.error('Unable perform "*IDN?" query.')
            exctype, value = sys.exc_info()[:2]
            self.lg.error(value)
            # try:
            #  sm.close()
            # except:
            #  pass
            self.lg.error(smCommsMsg)
            raise ValueError("Failed to talk to sourcemeter.")

        if self.idnContains in self.idn:
            if not self.quiet:
                self.lg.debug("Sourcemeter found:")
                self.lg.debug(self.idn)
            if not self.four88point1:
                self.check488point1(sm=sm)
        else:
            raise ValueError("Got a bad response to *IDN?: {:s}".format(self.idn))

        return sm

    # attempt to learn if the machine is in 488.1 mode (fast comms)
    def check488point1(self, sm=None):
        if sm == None:
            sm = self.sm
        try:
            if (sm.interface_type == pyvisa.constants.InterfaceType.gpib) and (sm.query(":system:mep:state?") == "0"):
                self.four88point1 = True
                self.lg.debug("High performance 488.1 comms mode activated!")
            else:
                self.four88point1 = False
        except:
            self.four88point1 = False

    def _setupSourcemeter(self, two_wire, front):
        """Do initial setup for sourcemeter"""
        sm = self.sm
        sm.timeout = self.default_comms_timeout  # long enough to collect an entire sweep [ms]
        self.auto_ohms = False

        sm.write(":status:preset")
        self.opc()
        sm.write(":trace:clear")
        self.opc()
        sm.write(":output:smode himpedance")
        self.opc()

        # set data transfer type
        if sm.interface_type == pyvisa.constants.InterfaceType.asrl:
            sm.write("format:data {:s}".format("ascii"))
        else:
            sm.write("format:data {:s}".format("sreal"))

        sm.write("source:clear:auto off")
        sm.write("source:voltage:protection 20")  # the instrument will never generate over 20v

        self.setWires(two_wire=two_wire)
        self.opc()
        sm.write(":sense:function:concurrent on")
        sm.write(':sense:function "current:dc", "voltage:dc"')
        sm.write(":format:elements time,voltage,current,status")
        # status is a 24 bit intiger. bits are these:
        # Bit 0 (OFLO) — Set to 1 if measurement was made while in over-range.
        # Bit 1 (Filter) — Set to 1 if measurement was made with the filter enabled.
        # Bit 2 (Front/Rear) — Set to 1 if FRONT terminals are selected.
        # Bit 3 (Compliance) — Set to 1 if in real compliance.
        # Bit 4 (OVP) — Set to 1 if the over voltage protection limit was reached.
        # Bit 5 (Math) — Set to 1 if math expression (calc1) is enabled.
        # Bit 6 (Null) — Set to 1 if Null is enabled.
        # Bit 7 (Limits) — Set to 1 if a limit test (calc2) is enabled.
        # Bits 8 and 9 (Limit Results) — Provides limit test results (see grading and sorting modes below).
        # Bit 10 (Auto-ohms) — Set to 1 if auto-ohms enabled.
        # Bit 11 (V-Meas) — Set to 1 if V-Measure is enabled.
        # Bit 12 (I-Meas) — Set to 1 if I-Measure is enabled.
        # Bit 13 (Ω-Meas) — Set to 1 if Ω-Measure is enabled.
        # Bit 14 (V-Sour) — Set to 1 if V-Source used.
        # Bit 15 (I-Sour) — Set to 1 if I-Source used.
        # Bit 16 (Range Compliance) — Set to 1 if in range compliance.
        # Bit 17 (Offset Compensation) — Set to 1 if Offset Compensated Ohms is enabled.
        # Bit 18 — Contact check failure (see AppendixF).
        # Bits 19, 20 and 21 (Limit Results) — Provides limit test results (see grading and sorting modes below).
        # Bit 22 (Remote Sense) — Set to 1 if 4-wire remote sense selected.
        # Bit 23 (Pulse Mode) — Set to 1 if in the Pulse Mode.

        # use front terminals?
        self.setTerminals(front=front)

        self.src = sm.query(":source:function:mode?")
        sm.write(":system:beeper:state off")
        sm.write(":system:lfrequency:auto on")
        sm.write(":system:time:reset")
        self.opc()

        sm.write(":system:azero off")  # we'll do this once before every measurement
        sm.write(":system:azero:caching on")
        self.opc()

        # enable/setup contact check :system:ccheck
        opts = self.sm.query("*opt?")
        if "CONTACT-CHECK" in opts.upper():
            sm.write(":system:ccheck off")
            sm.write(":system:ccheck:resistance 50")  # choices are 2, 15 or 50

    # note that this also checks the GUARD-SENSE connections, short those manually if not in use
    def set_ccheck_mode(self, value=True):
        if self.sm.query(":system:rsense?") == "1":
            opts = self.sm.query("*opt?")
            if "CONTACT-CHECK" in opts.upper():
                if value == True:
                    self.outOn(on=False)
                    self.sm.write(":output:smode guard")
                    self.sm.write(":system:ccheck on")
                    self.opc()
                    self.sm.write(":sense:voltage:nplcycles 0.1")
                    # setup I=0 voltage measurement
                    self.setupDC(sourceVoltage=False, compliance=3, setPoint=0, senseRange="f", auto_ohms=False)
                else:
                    self.sm.write(":output:smode himpedance")
                    self.outOn(on=False)
                    self.opc()
                    self.sm.write(f":sense:voltage:nplcycles {self.nplc_user_set}")
                    self.sm.write(":system:ccheck off")
            else:
                self.lg.debug("Contact check option not installed")
        else:
            self.lg.debug("Contact check function requires 4-wire mode")

    def setWires(self, two_wire=False):
        if two_wire:
            self.sm.write(":system:rsense off")  # four wire mode off
        else:
            self.sm.write(":system:rsense on")  # four wire mode on

    def setTerminals(self, front=False):
        if front:
            self.sm.write(":rout:term front")
        else:
            self.sm.write(":rout:term rear")

    def updateSweepStart(self, startVal):
        self.sm.write(":source:{:s}:start {:.8f}".format(self.src, startVal))

    def updateSweepStop(self, stopVal):
        self.sm.write(":source:{:s}:stop {:.8f}".format(self.src, stopVal))

    # sets the source to some value
    def setSource(self, outVal):
        self.sm.write(":source:{:s} {:.8f}".format(self.src, outVal))

    def write(self, toWrite):
        self.sm.write(toWrite)

    def outOn(self, on=True):
        if on:
            self.sm.write(":output on")
        else:
            self.sm.write(":output off")

    def getNPLC(self):
        return float(self.sm.query(":sense:current:nplcycles?"))

    def setNPLC(self, nplc):
        self.nplc_user_set = nplc
        self.sm.write(":sense:current:nplcycles {:}".format(nplc))
        self.sm.write(":sense:voltage:nplcycles {:}".format(nplc))
        self.opc()
        self.sm.write(":sense:resistance:nplcycles {:}".format(nplc))
        if nplc < 1:
            self.sm.write(":display:digits 5")
        else:
            self.sm.write(":display:digits 7")

    def setupDC(self, sourceVoltage=True, compliance=0.04, setPoint=0, senseRange="f", auto_ohms=False):
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
            sm.write(":sense:resistance:mode auto")
            self.opc()
            sm.write(":sense:resistance:range:auto on")
            # sm.write(':sense:resistance:range 20E3')
            sm.write(":format:elements voltage,current,resistance,time,status")
            self.auto_ohms = True
        else:
            self.auto_ohms = False
            sm.write(':sense:function:off "resistance"')
            if sourceVoltage:
                src = "voltage"
                snc = "current"
            else:
                src = "current"
                snc = "voltage"
            self.src = src
            self.opc()
            sm.write(":source:function {:s}".format(src))
            sm.write(":source:{:s}:mode fixed".format(src))
            sm.write(":source:{:s} {:.8f}".format(src, setPoint))

            sm.write(":source:delay:auto on")

            sm.write(':sense:function "{:s}"'.format(snc))
            sm.write(":sense:{:s}:protection {:.8f}".format(snc, compliance))

            self.opc()
            if senseRange == "f":
                sm.write(":sense:{:s}:range:auto off".format(snc))
                sm.write(":sense:{:s}:protection:rsynchronize on".format(snc))
            elif senseRange == "a":
                sm.write(":sense:{:s}:range:auto on".format(snc))
            else:
                sm.write(":sense:{:s}:range {:.8f}".format(snc, senseRange))

            # this again is to make sure the sense range gets updated
            sm.write(":sense:{:s}:protection {:.8f}".format(snc, compliance))
            sm.write(":format:elements voltage,current,time,status")
        self.opc()
        sm.write(":output on")
        sm.write(":trigger:count 1")

        sm.write(":system:azero once")
        self.opc()  # ensure the instrument is ready after all this

    def setupSweep(self, sourceVoltage=True, compliance=0.04, nPoints=101, stepDelay=-1, start=0, end=1, senseRange="f"):
        """setup for a sweep operation
        if senseRange == 'a' the instrument will auto range for both current and voltage measurements
        if senseRange == 'f' then the sense range will follow the compliance setting
        if stepDelay < 0 then step delay is on auto (~5ms), otherwise it's set to the value here (in seconds)
        """
        sm = self.sm
        self.opc()

        nplc = self.getNPLC()
        approx_measure_time = 1000 / 50 * nplc  # [ms] assume 50Hz line freq just because that's safer

        if sourceVoltage:
            src = "voltage"
            snc = "current"
        else:
            src = "current"
            snc = "voltage"
        self.src = src
        sm.write(":source:function {:s}".format(src))
        sm.write(":source:{:s} {:0.6f}".format(src, start))

        # seems to do exactly nothing
        # if snc == 'current':
        #  holdoff_delay = 0.005
        #  sm.write(':sense:current:range:holdoff on')
        #  sm.write(':sense:current:range:holdoff {:.6f}'.format(holdoff_delay))
        #  self.opc()  # needed to prevent input buffer overrun with serial comms (should be taken care of by flowcontrol!)

        sm.write(":sense:{:s}:protection {:.8f}".format(snc, compliance))

        if senseRange == "f":
            sm.write(":sense:{:s}:range:auto off".format(snc))
            sm.write(":sense:{:s}:protection:rsynchronize on".format(snc))
        elif senseRange == "a":
            sm.write(":sense:{:s}:range:auto on".format(snc))
        else:
            sm.write(":sense:{:s}:range {:.8f}".format(snc, senseRange))

        self.opc()
        # this again is to make sure the sense range gets updated
        sm.write(":sense:{:s}:protection {:.8f}".format(snc, compliance))

        sm.write(":output on")
        sm.write(":source:{:s}:mode sweep".format(src))
        sm.write(":source:sweep:spacing linear")
        if stepDelay < 0:
            # this just sets delay to 1ms (probably. the actual delay is in table 3-4, page 97, 3-13 of the k2400 manual)
            sm.write(":source:delay:auto on")
            approx_point_duration = 5 + approx_measure_time  # used for calculating dynamic sweep timeout [ms]
        else:
            sm.write(":source:delay:auto off")
            sm.write(f":source:delay {stepDelay:0.6f}")  # this value is in seconds!
            approx_point_duration = stepDelay * 1000 + approx_measure_time  # [ms] used for calculating dynamic sweep timeout
        self.opc()
        sm.write(":trigger:count {:d}".format(nPoints))
        sm.write(":source:sweep:points {:d}".format(nPoints))
        sm.write(":source:{:s}:start {:.6f}".format(src, start))
        sm.write(":source:{:s}:stop {:.6f}".format(src, end))
        if sourceVoltage:
            self.dV = abs(float(sm.query(":source:voltage:step?")))
        else:
            self.dI = abs(float(sm.query(":source:current:step?")))
        # sm.write(':source:{:s}:range {:.4f}'.format(src,max(start,end)))
        sm.write(":source:sweep:ranging best")
        # sm.write(':sense:{:s}:range:auto off'.format(snc))

        sm.write(":system:azero once")
        self.opc()  # ensure the instrument is ready after all this

        # calculate the expected sweep duration with safety margin
        max_sweep_duration = nPoints * approx_point_duration * 1.2  # [ms]

        # make sure long sweeps don't result in comms timeouts
        max_transport_time = 10000  # [ms] let's assume no sweep will ever take longer than 10s to transport
        sm.timeout = max_sweep_duration + max_transport_time  # [ms]

    def opc(self, sm=None):
        """returns when all operations are complete"""
        if self.four88point1 == False:
            opc_timeout = False
            if sm is None:
                sm = self.sm
            retries_left = 5
            tout = sm.timeout  # save old timeout
            sm.timeout = 2500  # in ms
            while retries_left > 0:
                cmd = "*WAI"
                bw = sm.write(cmd)
                if bw == (len(cmd) + 1):
                    one = "zero"
                    try:
                        one = sm.query("*OPC?")
                    except pyvisa.errors.VisaIOError:
                        opc_timeout = True  # need to handle this so we don't queue up OPC queries
                    if one == "1":
                        break
                retries_left = retries_left - 1
            sm.timeout = tout
            if retries_left == 0:
                raise (ValueError("OPC FAIL"))
            # we make sure there are no bytes left in the input buffer
            if opc_timeout == True:
                time.sleep(2.6)  # wait for the opc commands to unqueue
            self._flush_input_buffer(sm, delayms=500)

    def _stb(self, sm=None):
        if not self.four88point1:
            if sm == None:
                sm = self.sm
        return sm.query("*STB?")

    def _flush_input_buffer(self, sm, delayms=0):
        try:
            session = sm.visalib.sessions[sm._session]  # that's a pyserial object
            session.interface.reset_input_buffer()
        except Exception:
            pass
        if hasattr(sm, "bytes_in_buffer"):
            bib = sm.bytes_in_buffer
            while bib > 0:
                sm.read_raw(bib)  # toss the bytes
                time.sleep(delayms / 1000)
                bib = sm.bytes_in_buffer

    def arm(self):
        """arms trigger"""
        self.sm.write(":init")

    def trigger(self):
        """performs trigger event"""
        if self.sm.interface_type == pyvisa.constants.InterfaceType.gpib:
            self.sm.assert_trigger()
        else:
            self.sm.write("*TRG")

    def sendBusCommand(self, command):
        """sends a command over the GPIB bus
        See: https://linux-gpib.sourceforge.io/doc_html/gpib-protocol.html#REFERENCE-COMMAND-BYTES
        """
        if self.sm.interface_type == pyvisa.constants.InterfaceType.gpib:
            self.sm.send_command(command)
            # self.sm.send_command(0x08) # whole bus trigger
        else:
            self.lg.debug("Bus commands can only be sent over GPIB")

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
            vals = self.sm.query_ascii_values(":read?")
        else:
            if self.four88point1 == True:
                # this only works in 488.1 because it can use the read address line to initiate the measurement
                vals = self.sm.read_binary_values(data_points=nPoints * m_len, is_big_endian=True)
            else:
                vals = self.sm.query_binary_values(":read?", data_points=nPoints * m_len)

        # repackage this into tuples that are m_len long
        zi = zip(*[iter(vals)] * m_len)
        # put the tuples in a list and make sure status is an int
        if m_len == 4:
            reshaped = [(val[0], val[1], val[2], int(val[3])) for val in zi]
        elif m_len == 5:
            reshaped = [(val[0], val[1], val[2], val[3], int(val[4])) for val in zi]
        else:
            raise ValueError("unsupported data format")

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
            else:
                t_start = 0
                t_end = 0
            v_start = first_element[0]
            v_end = last_element[0]
            self.last_sweep_time = t_end - t_start
            stats_string = f"Sweep stats: avg. step voltage|duration|avg. point time|avg. rate-->{(v_start-v_end)/len(reshaped)*1000:0.2f}mV|{self.last_sweep_time:0.2f}s|{self.last_sweep_time/len(reshaped)*1000:0.0f}ms|{(v_start-v_end)/self.last_sweep_time:0.3f}V/s"
            if self.print_sweep_deets:
                self.lg.log(29, stats_string)
            else:
                self.lg.debug(stats_string)
            self.sm.timeout = self.default_comms_timeout  # reset comms timeout to default value after sweep

        # update the status byte
        self.status = int(reshaped[-1][-1])
        return reshaped

    def measureUntil(self, t_dwell=float("Infinity"), measurements=float("Infinity"), cb=lambda x: None):
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
        # self.opc() # before we start reading, ensure the device is ready
        while (i < measurements) and (time.time() < t_end) and (not self.killer.is_set()):
            i = i + 1
            measurement = self.measure()
            q.append(measurement[0])
            cb(measurement)
        if self.killer.is_set():
            self.lg.debug("Killed by killer")
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
        self.sm.write(":output on")  # try to turn on the output
        if self.sm.query(":output?") == "1":  # check if that worked
            self.sm.write("INIT")
            time.sleep(0.1)  # TODO: figure out a better way to do this. mysterious dealys = bad
            if self.sm.query(":output?") == "1":
                good_contact = True  # if INIT didn't trip the output off, then we're connected
        return good_contact


# testing code
if __name__ == "__main__":
    import pandas as pd
    import numpy as np

    start = time.time()

    # address = "GPIB0::24::INSTR"
    # address = 'TCPIP0::10.45.0.186::4000::SOCKET'
    # address = 'ASRL/dev/ttyS0::INSTR'
    schema = "hw://"
    port = "/dev/ttyS0"
    options = {}
    options["baudrate"] = 57600
    options["bytesize"] = "EIGHTBITS"
    options["parity"] = "PARITY_NONE"
    options["stopbits"] = "STOPBITS_ONE"
    options["timeout"] = 1
    options["xonxoff"] = False
    options["rtscts"] = False
    options["dsrdtr"] = False
    options["write_timeout"] = 1
    options["inter_byte_timeout"] = 1
    address = f"{schema}{port}?{'&'.join([f'{a}={b}' for a, b in options.items()])}"

    k = k2400(address=address)
    k.connect()

    con_time = time.time()
    print(f"Connected to {k.address} in {con_time-start} seconds")

    # do a contact check
    k.set_ccheck_mode(True)
    print(f"Contact check result: {k.contact_check()}")
    k.set_ccheck_mode(False)

    # setup DC resistance measurement
    k.setupDC(auto_ohms=True)

    print(f"One auto ohms measurement: {k.measure()}")

    # measure
    mTime = 10
    k.setNPLC(1)
    dc_m = k.measureUntil(t_dwell=mTime)

    # create a custom data type to hold our data
    measurement_datatype = np.dtype({"names": ["voltage", "current", "resistance", "time", "status"], "formats": ["f", "f", "f", "f", "u4"], "titles": ["Voltage [V]", "Current [A]", "Resistance [Ohm]", "Time [s]", "Status bitmask"]})

    # convert the data to a numpy array
    dc_ma = np.array(dc_m, dtype=measurement_datatype)

    # convert the data to a pandas dataframe and print it
    dc_mf = pd.DataFrame(dc_ma)
    print(f"===== {len(dc_mf)} auto ohms values in {mTime} seconds =====")
    # print(dc_mf.to_string(formatters={'status':'{0:024b}'.format}))

    # setup DC current measurement at 0V measurement
    forceV = 0
    k.setupDC(setPoint=forceV)

    print(f"One V=0 measurement: {k.measure()}")

    # measure
    mTime = 10
    k.setNPLC(0.01)
    dc_m = k.measureUntil(t_dwell=mTime)

    # create a custom data type to hold our data
    measurement_datatype = np.dtype({"names": ["voltage", "current", "time", "status"], "formats": ["f", "f", "f", "u4"], "titles": ["Voltage [V]", "Current [A]", "Time [s]", "Status bitmask"]})

    # convert the data to a numpy array
    dc_ma = np.array(dc_m, dtype=measurement_datatype)

    # convert the data to a pandas dataframe and print it
    dc_mf = pd.DataFrame(dc_ma)
    print(f"===== {len(dc_mf)} DC V={forceV} values in {mTime} seconds =====")
    # print(dc_mf.to_string(formatters={'status':'{0:024b}'.format}))

    # now for a 101 point voltage sweep from 0 --> 1V
    numPoints = 101
    startV = 0
    endV = 1
    k.setupSweep(compliance=0.01, nPoints=numPoints, start=startV, end=endV)  # set the sweep up
    t0 = time.time()
    # TODO: need to understand why the actual sweep here when done in serial mode is so much slower (not the comms)
    sw_m = k.measure(nPoints=numPoints)  # make the measurement
    tend = time.time() - t0

    # convert the result to a numpy array
    sw_ma = np.array(sw_m, dtype=measurement_datatype)

    # convert the result to a pandas dataframe and print it
    sw_mf = pd.DataFrame(sw_ma)
    print(f"===== {len(sw_ma)} point sweep event from V={startV} to V={endV} completed in {tend:.2f}s ({k.last_sweep_time:.2f}s sweeping and {tend-k.last_sweep_time:2f}s data transfer) =====")
    # print(dc_mf.to_string(formatters={'status':'{0:024b}'.format}))

    # shut off the output
    k.outOn(False)

    k.disconnect()  # TODO: switch to context manager for proper cleanup

    print(f"Total Time = {time.time()-start} seconds")
