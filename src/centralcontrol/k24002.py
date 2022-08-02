#!/usr/bin/env python

import sys
import time
import serial
import threading
import socket

try:
    from centralcontrol.logstuff import get_logger as getLogger
except:
    from logging import getLogger


class k2400(object):
    """
    Intertace for Keithley 2400 sourcemeter
    """

    expect_in_idn = "KEITHLEY"
    quiet = False
    idn = ""
    opts = ""
    status = 0
    nplc_user_set = 1.0
    last_sweep_time: float = 0
    readyForAction = False
    four88point1 = False
    print_sweep_deets: bool = False  # false uses debug logging level, true logs sweep stats at info level
    _write_term_str = "\n"
    _read_term_str = "\r"
    connected = False
    ser: serial.Serial = None
    timeout: float = None  # default comms timeout
    do_r: bool = False  # include resistance in measurement

    def __init__(self, address: str, terminator="\r", serial_baud=57600, front=True, two_wire=True, quiet=False, killer=threading.Event(), print_sweep_deets=False, **kwargs):
        """just set class variables here"""

        self.lg = getLogger(".".join([__name__, type(self).__name__]))  # setup logging

        self.killer = killer
        self.quiet = quiet
        self.address = address
        self.terminator = terminator
        self.serial_baud = serial_baud
        self.front = front
        self.two_wire = two_wire
        self.print_sweep_deets = print_sweep_deets
        self.write_term = bytes([ord(x) for x in self._write_term_str])
        self.read_term = bytes([ord(x) for x in self._read_term_str])
        self.write_term_len = len(self.write_term)
        self.read_term_len = len(self.read_term)
        self.connected = False

        # add some features to pyserial's address URL handling
        # trigger this through the use of a hwurl:// schema
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

        self.lg.debug("k2400 initialized.")

    def __enter__(self) -> "k2400":
        """so that the smu can enter a context"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        """so that the smu can leave a context cleanly"""
        self.disconnect()
        return False

    @property
    def write_term_str(self):
        return self._write_term_str

    @write_term_str.setter
    def write_term_str(self, value):
        self._write_term_str = value
        self.write_term = bytes([ord(x) for x in self._write_term_str])
        self.write_term_len = len(self.write_term)

    @property
    def read_term_str(self):
        return self._read_term_str

    @read_term_str.setter
    def read_term_str(self, value):
        self._read_term_str = value
        self.read_term = bytes([ord(x) for x in self._read_term_str])
        self.read_term_len = len(self.read_term)

    def dead_socket_cleanup(self, host):
        """attempts dead socket cleanup on a 2450"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.connect((host, 5030))
                s.sendall(b"goodbye")
                s.shutdown(socket.SHUT_RDWR)
                s.close()
        except:
            pass

    def hard_input_buffer_reset(self):
        """does graceful then hard input buffer resets"""
        # brute force discard
        self.ser.timeout = 0.2
        try:
            while True:
                got = self.ser.read()
                if len(got) == 0:
                    break
        except:
            pass
        self.ser.timeout = self.timeout

    def connect(self):
        """attempt to connect to hardware and initialize it"""

        if "socket" in self.address:
            self.read_term_str = "\n"
            kwargs = {"timeout": 1.0}
            hostport = self.address.removeprefix("socket://")
            [self.host, _] = hostport.split(":", 1)
            self.dead_socket_cleanup(self.host)
        else:
            kwargs = {}

        try:
            self.ser = serial.serial_for_url(self.address, **kwargs)
        except Exception as e:
            raise ValueError(f"Failure connecting to {self.address} with: {e}")
        else:
            self.connected = self.ser.is_open
        self.ser.reset_output_buffer()

        if self.ser.xonxoff:
            self.ser.send_break()
            one = self.ser.write(bytes([17]))  # XON
            if one != 1:
                raise ValueError(f"Serial send failure.")

        self.ser.send_break()
        one = self.ser.write(bytes([18]))  # interrupt
        # ser.break_condition = False
        if one != 1:
            raise ValueError(f"Serial send failure.")

        self.ser.send_break()
        # discard the input buffer
        self.ser.reset_input_buffer()
        self.hard_input_buffer_reset()  # for discarding currently streaming data
        self.hardware_reset()
        # really make sure the buffer's clean befo
        self.hard_input_buffer_reset()  # for discarding currently streaming data

        # tests the ROM's checksum. can take over a second
        self.timeout = self.ser.timeout
        self.ser.timeout = 5
        zero = self.query("*TST?")
        if zero != "0":
            raise ValueError(f"Self test failed: {zero}")
        self.ser.timeout = self.timeout

        self.setup(self.front, self.two_wire)

        self.lg.debug("k2400 connected.")

        return 0

    def setup(self, front=True, two_wire=False):
        """does baseline configuration in prep for data collection"""
        self.idn = self.query("*IDN?")  # ask the device to identify its self
        self.write("outp:smod himp")  # outputs go to high impedance when switched off
        self.write("sour:volt:prot 20")  # limit the voltage output (in all modes) for safety
        self.setWires(two_wire)
        self.write("sens:func 'curr:dc', 'volt:dc'")
        self.write("form:elem time,volt,curr,stat")  # set what we want reported
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
        self.setTerminals(front)

        self.src = self.query("sour:func:mode?")  # check/set the source
        self.write("syst:azer off")  # we'll do this once before every measurement
        self.write("syst:azer:cach 1")

        # enable/setup contact check :system:ccheck
        self.opts = self.query("*OPT?")
        if "CONTACT-CHECK" in self.opts.upper():
            self.write("syst:cch 0")  # disable feature
            # self.write("syst:cch:res 50")  # choices are 2, 15 or 50 (50 is default)

        self.write("syst:time:res")  # reset the internal timer
        self.lg.debug("k2400 setup complete.")

    def opc(self) -> bool:
        """asks the hardware to finish whatever it's doing then send a 1"""
        retries = 5
        for i in range(retries):
            opc_val = self.query("*OPC?")
            if opc_val == "1":
                ret = True
                break
            else:
                ret = False
        if not ret:
            self.lg.debug(f"*OPC? gave: {opc_val}")
        return ret

    def write(self, cmd):
        cmd_bytes = len(cmd)
        bytes_written = self.ser.write(cmd.encode() + self.write_term)
        if cmd_bytes != (bytes_written - self.write_term_len):
            raise ValueError(f"Write failure, {bytes_written - self.write_term_len} != {cmd_bytes}")

    def query(self, question: str) -> str:
        self.write(question)
        return self.read()

    def read(self) -> str:
        return self.ser.read_until(self.read_term).decode().removesuffix(self.read_term_str)

    def hardware_reset(self):
        """attempt to stop everything and put the hardware into a known baseline state"""
        try:
            self.ser.send_break()
        except:
            pass

        try:
            self.ser.write(bytes([18]))  # interrupt
        except:
            pass

        try:
            self.ser.send_break()
        except:
            pass

        # the above breaks can generate DCLs so lets discard those
        try:
            self.ser.reset_input_buffer()
        except:
            pass

        try:
            self.write("*RST")  # GPIB defaults
        except:
            pass
        else:
            self.src = "volt"

        try:
            self.opc()
        except:
            pass

        try:
            self.write("syst:pres")  # factory defaults
        except:
            pass
        else:
            self.src = "volt"

        try:
            self.opc()
        except:
            pass

        try:
            self.write("*CLS")  # reset registers
        except:
            pass

        try:
            self.write("*ESE 0")  # reset registers
        except:
            pass

        try:
            self.write("stat:pres")  # reset more registers
        except:
            pass

        try:
            self.write("stat:que:cle")  # clear error queue
        except:
            pass

        try:
            self.write("syst:beep:stat 0")  # the beeps are annoying
        except:
            pass

        try:
            self.write("syst:lfr:auto 1")  # auto line frequency on
        except:
            pass

        try:
            self.write("trac:cle")  # clear trace/data buffer
        except:
            pass

        try:
            self.opc()
        except:
            pass

        try:
            self.ser.reset_input_buffer()
        except:
            pass

    def disconnect(self):
        """do our best to close down and clean up the instrument"""

        self.hardware_reset()

        # going local this way is only possible from rs232 on an og 2400
        # so we won't do that
        # try:
        #     self.write("syst:loc")
        # except:
        #     pass

        try:
            self.ser.close()
        except:
            pass
        else:
            self.connected = self.ser.is_open

        # use dead socket port to clean up old connections
        if "socket" in self.address:
            self.dead_socket_cleanup(self.host)

    # note that this also checks the GUARD-SENSE connections, short those manually if not in use
    def set_ccheck_mode(self, value: bool = True):
        if self.query("syst:rsen?") == "1":
            if "CONTACT-CHECK" in self.opts.upper():
                if value:
                    self.outOn(on=False)
                    self.write("outp:smod guar")
                    self.write("syst:cch 1")
                    self.write("sens:volt:nplc 0.1")
                    # setup I=0 voltage measurement
                    self.setupDC(sourceVoltage=False, compliance=3, setPoint=0, senseRange="f", auto_ohms=False)
                else:
                    self.write("outp:smod himp")
                    self.outOn(on=False)
                    self.write(f"sens:volt:nplc {self.nplc_user_set}")
                    self.write("syst:cch 0")
            else:
                self.lg.debug("Contact check option not installed")
        else:
            self.lg.debug("Contact check function requires 4-wire mode")

    def setWires(self, two_wire=False):
        if two_wire:
            self.write("syst:rsen 0")  # four wire mode off
        else:
            self.write("syst:rsen 1")  # four wire mode on

    def setTerminals(self, front=False):
        if front:
            self.write("rout:term fron")
        else:
            self.write("rout:term rear")

    def updateSweepStart(self, startVal):
        self.write("source:{:s}:start {:.8f}".format(self.src, startVal))

    def updateSweepStop(self, stopVal):
        self.write("source:{:s}:stop {:.8f}".format(self.src, stopVal))

    # sets the source to some value
    def setSource(self, outVal):
        self.write("source:{:s} {:.8f}".format(self.src, outVal))

    def outOn(self, on=True):
        if on:
            self.write("outp 1")
        else:
            self.write("outp 0")

    def getNPLC(self):
        return float(self.query("sense:curr:nplc?"))

    def setNPLC(self, nplc: float):
        self.nplc_user_set = nplc
        self.write(f"sens:curr:nplc {nplc}")
        self.write(f"sens:volt:nplc {nplc}")
        self.write(f"sens:res:nplc {nplc}")
        if nplc < 1:
            self.write("display:digits 5")
        else:
            self.write("display:digits 7")

    def setupDC(self, sourceVoltage=True, compliance=0.04, setPoint=0, senseRange="f", auto_ohms=False):
        """setup DC measurement operation
        if senseRange == 'a' the instrument will auto range for both current and voltage measurements
        if senseRange == 'f' then the sense range will follow the compliance setting
        if sourceVoltage == False, we'll have a current source at setPoint amps with max voltage +/- compliance volts
        auto_ohms = true will override everything and make the output data change to (voltage,current,resistance,time,status)
        """

        if auto_ohms == True:
            self.write(':sense:function:on "resistance"')
            self.write(":sense:resistance:mode auto")
            self.write(":sense:resistance:range:auto on")
            # sm.write(':sense:resistance:range 20E3')
            self.write(":format:elements voltage,current,resistance,time,status")
        else:
            self.write(':sense:function:off "resistance"')
            if sourceVoltage:
                src = "voltage"
                snc = "current"
            else:
                src = "current"
                snc = "voltage"
            self.src = src
            self.write(":source:function {:s}".format(src))
            self.write(":source:{:s}:mode fixed".format(src))
            self.write(":source:{:s} {:.8f}".format(src, setPoint))

            self.write(":source:delay:auto on")

            self.write(':sense:function "{:s}"'.format(snc))
            self.write(":sense:{:s}:protection {:.8f}".format(snc, compliance))

            if senseRange == "f":
                self.write(":sense:{:s}:range:auto off".format(snc))
                self.write(":sense:{:s}:protection:rsynchronize on".format(snc))
            elif senseRange == "a":
                self.write(":sense:{:s}:range:auto on".format(snc))
            else:
                self.write(":sense:{:s}:range {:.8f}".format(snc, senseRange))

            # this again is to make sure the sense range gets updated
            self.write(":sense:{:s}:protection {:.8f}".format(snc, compliance))
            self.write(":format:elements voltage,current,time,status")

        self.do_r = auto_ohms
        self.outOn()
        self.write("trigger:count 1")

        self.do_azer()

    def setupSweep(self, sourceVoltage=True, compliance=0.04, nPoints=101, stepDelay=-1, start=0, end=1, senseRange="f"):
        """setup for a sweep operation
        if senseRange == 'a' the instrument will auto range for both current and voltage measurements
        if senseRange == 'f' then the sense range will follow the compliance setting
        if stepDelay < 0 then step delay is on auto (~5ms), otherwise it's set to the value here (in seconds)
        """

        nplc = self.getNPLC()
        approx_measure_time = 1000 / 50 * nplc  # [ms] assume 50Hz line freq just because that's safer

        if sourceVoltage:
            src = "voltage"
            snc = "current"
        else:
            src = "current"
            snc = "voltage"
        self.src = src
        self.write("sour:func {:s}".format(src))
        self.write("sour:{:s} {:0.6f}".format(src, start))

        # seems to do exactly nothing
        # if snc == 'current':
        #  holdoff_delay = 0.005
        #  sm.write(':sense:current:range:holdoff on')
        #  sm.write(':sense:current:range:holdoff {:.6f}'.format(holdoff_delay))
        #  self.opc()  # needed to prevent input buffer overrun with serial comms (should be taken care of by flowcontrol!)

        self.write("sens:{:s}:prot {:.8f}".format(snc, compliance))

        if senseRange == "f":
            self.write("sens:{:s}:range:auto 0".format(snc))
            self.write("sens:{:s}:prot:rsyn 1".format(snc))
        elif senseRange == "a":
            self.write("sens:{:s}:range:auto on".format(snc))
        else:
            self.write("sens:{:s}:range {:.8f}".format(snc, senseRange))

        # this again is to make sure the sense range gets updated
        self.write("sens:{:s}:prot {:.8f}".format(snc, compliance))

        self.outOn()
        self.write("sour:{:s}:mode sweep".format(src))
        self.write("sour:sweep:spacing linear")
        if stepDelay < 0:
            # this just sets delay to 1ms (probably. the actual delay is in table 3-4, page 97, 3-13 of the k2400 manual)
            self.write("sour:delay:auto 1")
            approx_point_duration = 20 + approx_measure_time  # used for calculating dynamic sweep timeout [ms]
        else:
            self.write("sour:delay:auto 0")
            self.write(f"sour:delay {stepDelay:0.6f}")  # this value is in seconds!
            approx_point_duration = 20 + stepDelay * 1000 + approx_measure_time  # [ms] used for calculating dynamic sweep timeout

        self.write("trigger:count {:d}".format(nPoints))
        self.write("sour:sweep:points {:d}".format(nPoints))
        self.write("sour:{:s}:start {:.6f}".format(src, start))
        self.write("sour:{:s}:stop {:.6f}".format(src, end))

        # relax the timeout since the above can take a bit longer to process
        self.ser.timeout = 5
        if sourceVoltage:
            self.dV = abs(float(self.query("sour:volt:step?")))
        else:
            self.dI = abs(float(self.query("source:curr:step?")))
        self.ser.timeout = self.timeout  # restore default timeout
        # sm.write(':source:{:s}:range {:.4f}'.format(src,max(start,end)))
        self.write("sour:sweep:ranging best")
        # sm.write(':sense:{:s}:range:auto off'.format(snc))

        self.do_azer()

        # calculate the expected sweep duration with safety margin
        max_sweep_duration = nPoints * approx_point_duration * 1.2  # [ms]

        # make sure long sweeps don't result in comms timeouts
        max_transport_time = 10000  # [ms] let's asssetupSume no sweep will ever take longer than 10s to transport
        self.ser.timeout = (max_sweep_duration + max_transport_time) / 1000  # [s]

    def do_azer(self):
        """parform autozero routine"""
        self.write("syst:azer once")
        self.opc()  # ensure the instrument is ready after all this

    def arm(self):
        """arms trigger"""
        self.write(":init")

    def trigger(self):
        """performs trigger event"""
        self.write("*TRG")

    def measure(self, nPoints: int = 1) -> list[tuple[float, float, float, int]] | list[tuple[float, float, float, float, int]]:
        """Makes a measurement and returns the result
        returns a list of measurements
        a "measurement" is a tuple of length 4: voltage,current,time,status (or length 5: voltage,current,resistance,time,status if dc setup was done in ohms mode)
        for a prior DC setup, the list will be 1 long.
        for a prior sweep setup, the list returned will be n sweep points long
        """

        # figure out how many points per sample we expect
        if self.do_r:
            pps = 5
        else:
            pps = 4
        vals = []
        self.write(":read?")  # trigger measurement
        red = self.read()
        red_nums = [float(x) for x in red.split(",")]
        for i in range(nPoints):
            line = []
            for j in range(pps):
                line.append(red_nums[i * pps + j])
            line[-1] = int(line[-1])  # status is an int and it's always last
            vals.append(tuple(line))

        # if this was a sweep, compute how long it took
        if nPoints > 1:
            first_element = vals[0]
            last_element = vals[-1]
            if pps == 4:
                t_start = first_element[2]
                t_end = last_element[2]
            elif pps == 5:
                t_start = first_element[3]
                t_end = last_element[3]
            else:
                t_start = 0
                t_end = 0
            v_start = first_element[0]
            v_end = last_element[0]
            self.last_sweep_time = t_end - t_start
            n_vals = len(vals)
            stats_string = f"Sweep stats: avg. step voltage|duration|avg. point time|avg. rate-->{(v_start-v_end)/n_vals*1000:0.2f}mV|{self.last_sweep_time:0.2f}s|{self.last_sweep_time/n_vals*1000:0.0f}ms|{(v_start-v_end)/self.last_sweep_time:0.3f}V/s"
            if self.print_sweep_deets:
                self.lg.log(29, stats_string)
            else:
                self.lg.debug(stats_string)
            # reset comms timeout to default value after sweep
            self.ser.timeout = self.timeout

        # update the status byte
        self.status = vals[-1][-1]
        return vals

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
        self.outOn()  # try to turn on the output
        if self.query(":output?") == "1":  # check if that worked
            self.write("init")
            time.sleep(0.1)  # TODO: figure out a better way to do this. mysterious dealys = bad
            if self.query(":output?") == "1":
                good_contact = True  # if INIT didn't trip the output off, then we're connected
        return good_contact