#!/usr/bin/env python

import sys
import time
import serial
from threading import Event as tEvent
from multiprocessing.synchronize import Event as mEvent
import socket
import re

import logging
from centralcontrol.logstuff import get_logger


class k2xxx(object):
    """Intertace for Keithley 2xxx sourcemeter"""
    __IDN_KIND_DETECT = (
        {"series": "2400",  "model": "2400",  "re": ".*Keithley.*Model 24(00|01|10|20|40),.*"},
        {"series": "2400G", "model": "2450",  "re": ".*Keithley.*Model 24(50|60|61|70),.*"},
        {"series": "2600",  "model": "2601",  "re": ".*Keithley.*Model 26(01|11|35),.*"},
        {"series": "2600",  "model": "2602",  "re": ".*Keithley.*Model 26(02|12|36),.*"},
        {"series": "2600B", "model": "2601B", "re": ".*Keithley.*Model 26(01|11|35)B,.*"},
        {"series": "2600B", "model": "2602B", "re": ".*Keithley.*Model 26(02|04|12|14|34|36)B,.*"},
    )

    idn = ""  # response to *IDN?
    series = ""  # the SMU acts like this series (from the __IDN_KIND_DETECT list)
    model = ""  # the SMU acts like this model (from the __IDN_KIND_DETECT list)
    opts = ""
    status = 0
    nplc_user_set = 1.0
    last_sweep_time: float = 0.0
    readyForAction = False
    four88point1 = False
    print_sweep_deets = False  # false uses debug logging level, true logs sweep stats at info level
    connected = False
    ser: serial.Serial
    do_r: str | bool = False  # include resistance in measurement
    t_relay_bounce = 0.05  # number of seconds to wait to ensure the contact check relays have stopped bouncing
    last_lo = None  # we're not set up for contact checking
    cc_mode = "none"  # contact check mode
    front = True
    two_wire = True
    killer: tEvent | mEvent
    address: str = ""
    threshold_ohm = 33.3  # resistance values below this give passing contact checker tests
    connect_kwargs: dict
    __write_term_str = "\n"
    __write_term_bytes = b'\n'
    __write_term_len = 1
    __read_term_str = "\r"
    __read_term_bytes = b'\n'
    __read_term_len = 1
    __sockethost:str = ""
    __socketport:int = 0
    __timeout:float|None  # comms timout (read only through timeout property)
    __src:str = ""  # keeps track of volt/curr source mode of hardware
    __srcs:list[str]  # same as __src, except for multichannel

    def __init__(self, address:str, front:bool=front, two_wire:bool=two_wire, killer:tEvent|mEvent=tEvent(), print_sweep_deets:bool=print_sweep_deets, cc_mode:str=cc_mode, read_term:str=__read_term_str, write_term:str=__write_term_str, **kwargs):
        """just set class variables here"""
        self.lg = get_logger(".".join([__name__, type(self).__name__]))  # setup logging
        self.lg.debug("k2xxx init starting")
        self.__srcs = [""]

        self.address = address
        self.front = front
        self.two_wire = two_wire
        self.killer = killer
        self.print_sweep_deets = print_sweep_deets
        self.write_term = write_term
        self.read_term = read_term
        self.cc_mode = cc_mode
        self.connect_kwargs = kwargs  # use the the rest of the keyword argumests here in connect()

        self.lg.debug("hwurl:// schema setup starting")
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

        self.lg.debug("registering new hwurl:// handler")
        sys.modules["hwurl"] = HWURL
        sys.modules["hwurl.protocol_hw"] = HWURL
        serial.protocol_handler_packages.append("hwurl")

        self.lg.debug("k2xxx initialized.")

    def __enter__(self) -> "k2xxx":
        """so that the smu can enter a context"""
        self.lg.debug("Entering context")
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        """so that the smu can leave a context cleanly"""
        self.lg.debug("Exiting context")
        self.disconnect()
        return False

    @property
    def timeout(self) -> float|None:
        """comms timeout (read only. set it with the timeout kwarg passed to init)"""
        return self.__timeout

    @property
    def write_term(self) -> str:
        return self.__write_term_str

    @write_term.setter
    def write_term(self, value:str):
        self.__write_term_str = value
        self.__write_term_bytes = bytes([ord(x) for x in value])
        self.__write_term_len = len(value)

    @property
    def write_term_len(self) -> int:
        return self.__write_term_len

    @property
    def read_term(self) -> str:
        return self.__read_term_str

    @read_term.setter
    def read_term(self, value:str):
        self.__read_term_str = value
        self.__read_term_bytes = bytes([ord(x) for x in value])
        self.__read_term_len = len(value)

    @property
    def read_term_len(self) -> int:
        return self.__read_term_len

    def dead_socket_cleanup(self, host:str):
        """attempts dead socket cleanup on a 2450 via the dead socket port"""
        dead_socket_port = 5030
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.connect((host, dead_socket_port))
                s.settimeout(0.5)
                s.sendall(b"goodbye")
                s.shutdown(socket.SHUT_RDWR)
                self.hard_input_buffer_reset(s)
        except Exception as e:
            self.lg.debug(f"Dead socket cleanup issue: {e}")

    def socket_cleanup(self, host:str, port:int):
        """ensure a the host/port combo is clean and closed"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.connect((host, port))
                s.settimeout(0.5)
                s.shutdown(socket.SHUT_RDWR)
                self.hard_input_buffer_reset(s)
        except Exception as e:
            self.lg.debug(f"Socket cleanup issue: {e}")

    def hard_input_buffer_reset(self, s=None) -> bool:
        """brute force input buffer discard with failure check"""
        if isinstance(s, socket.socket):
            oto = s.gettimeout()
            s.settimeout(0.5)
            fetcher = lambda: s.recv(16)
        elif self.ser is not None:
            oto = self.ser.timeout  # save timeout value
            fetcher = self.ser.read
            self.ser.timeout = 0.2
        else:
            return False

        try:
            while len(fetcher()) != 0:  # chuck anything that was sent to us
                pass
        except TimeoutError:
            success = True  # timeouts are ok
        except Exception as e:
            success = False  # abnormal read result
        else:
            success = True  # normal read result

        # restore previous timeout
        if isinstance(s, socket.socket):
            s.settimeout(oto)
        elif self.ser:
            self.ser.timeout = oto

        return success

    def connect(self):
        """attempt to connect to hardware and initialize it"""

        remaining_connection_retries = 5
        while remaining_connection_retries > 0:
            if "socket" in self.address:
                self.read_term_str = "\n"
                hostport = self.address.removeprefix("socket://")
                [sockethost, socketport] = hostport.split(":", 1)
                self.__sockethost = sockethost
                self.__socketport = int(socketport)
                self.lg.debug(f"Cleaning up socket: {[self.__sockethost, self.__socketport]}")
                self.socket_cleanup(self.__sockethost, self.__socketport)  # NOTE: this might cause trouble
                self.dead_socket_cleanup(self.__sockethost)  # NOTE: this might cause trouble
                self.socket_cleanup(self.__sockethost, self.__socketport)  # NOTE: this might cause trouble
                time.sleep(0.5)  # TODO: remove this hack  (but it adds stability)
                self.lg.debug(f"Socket clean.")

            try:
                self.ser = serial.serial_for_url(self.address, **self.connect_kwargs)
                self.lg.debug(f"Connection opened: {self.address}")
                if ("socket" in self.address):
                    # set the initial timeout to something long for setup
                    self.ser._socket.settimeout(5.0)  #TODO: try just setting self.ser.timeout = 5
                    time.sleep(0.5)  # TODO: remove this hack  (but it adds stability)
            except Exception as e:
                raise ValueError(f"Failure connecting to {self.address} with: {e}")

            if self.hard_input_buffer_reset():  # this checks for a "ghost connection"
                self.connected = self.ser.is_open
                break  # not a ghost connection, exit connection retry loop
            else:  # ghost connection!
                remaining_connection_retries -= 1
                self.lg.debug(f"Connection retries remaining: {remaining_connection_retries}")
                self.disconnect()
        else:
            raise ValueError(f"Connection retries exhausted while connecting to {self.address}")

        self.__timeout = self.ser.timeout  # store away the timeout

        self.ser.reset_output_buffer()

        # if we're using hardware flow control here, assert the RTS line
        if self.ser.rtscts:
            self.ser.rts = True

        # if we're using software flow control here, send an XON
        if self.ser.xonxoff:
            self.cts()
            self.ser.send_break()
            self.cts()
            one = self.ser.write(bytes([17]))  # XON
            if one != 1:
                raise ValueError(f"Serial send failure.")

        self.cts()
        self.ser.send_break()
        self.cts()
        one = self.interrupt()  # interrupt
        # ser.break_condition = False
        if one != 1:
            raise ValueError(f"Serial send failure.")

        self.cts()
        self.ser.send_break()
        # discard the input buffer
        self.ser.reset_input_buffer()
        self.hard_input_buffer_reset()  # for discarding currently streaming data
        self.hardware_reset()  # instrument is identified in here
        # really make sure the buffer's clean
        self.hard_input_buffer_reset()  # for discarding currently streaming data

        # test if we're running the newer "Graphical Series"
        if self.series == "2400G":
            # ensure we're using SCPI2400 language set
            try:
                self.lg.debug("Checking language set...")
                lang = self.query("*LANG?")
                if "2400" not in lang:
                    self.lg.debug(f"Found a bad language set: {lang}")
                    self.lg.debug(f"Attempting language set change")
                    self.write("*LANG SCPI2400")
                    self.lg.error(f"Please manually power cycle the SMU at address {self.address} now to complete a language set change.")
                    raise ValueError(f"Bad SMU language set: {lang}")
                else:
                    self.lg.debug(f"Found good language set: {lang}")
            except Exception as e:
                self.lg.debug(f"Exception: {repr(e)}")

        # tests the ROM's checksum. can take over a second
        self.ser.timeout = 5
        zero = self.query("*TST?")
        if zero != "0":
            raise ValueError(f"Self test failed: {zero}")
        self.ser.timeout = self.timeout  # restore the default timeout

        self.setup(self.front, self.two_wire)

        # TODO: get rid of this in favor of setting the timeout in the init kwargs
        if "socket" in self.address:
            # timeout for normal operation will be shorter
            self.ser._socket.settimeout(1.0)

        self.lg.debug(f"k2xxx connected.")

        return 0

    def interrupt(self) -> int|None:
        self.cts()
        return self.ser.write(bytes([18]))

    def cts(self):
        """wait for cts"""
        t0 = time.time()
        while not self.ser.cts:
            time.sleep(0.1)
            if self.timeout and ((time.time() - t0) > self.timeout):
                raise TimeoutError("Timeout waiting for cts signal.")

    def identify(self):
        # ask the device to identify itself
        self.idn = self.query("*IDN?")

        matched = False
        for idn_line in self.__IDN_KIND_DETECT:
            if re.fullmatch(idn_line["re"], self.idn):
                matched = True
                self.series = idn_line["series"]
                self.model = idn_line["model"]
                self.lg.debug(f"SMU matches {self.model}")
                break

        if not matched:
            raise RuntimeError(f"Unsupported SMU IDN: {self.idn}")

    def config_buffers(self, conf_strs:list[str]):
        """config all buffers (tsp command)"""
        if self.series in ("2600",):
            buffers = ("nvbuffer1", "nvbuffer2")
            chans = ["smua"]
            if self.model in ("2602",):
                chans.append("smub")
            for chan in chans:
                for buffer in buffers:
                    self.write(f"{chan}.{buffer}.clear()")
                    for conf_str in conf_strs:
                        self.write(f"{chan}.{buffer}.{conf_str}")

    def setup(self, front=True, two_wire=False):
        """does baseline configuration in prep for data collection"""

        if self.model == "2450":
            if self.query("syst:tlin?") != "0":
                self.lg.debug("Switching DIO port state to match 240x series")
                self.write("syst:tlin 0")  # dio lines on 245x to mimic 240x series

        # outputs go to high impedance when switched off
        if self.series in ("2400", "2400G"):
            self.write("outp:smod himp")
        elif self.series in ("2600",):
            chans = ["smua"]
            if self.model in ("2602",):
                chans.append("smub")
            for chan in chans:
                self.write(f"{chan}.source.offmode = {chan}.OUTPUT_HIGH_Z")

        # limit the voltage output (in all modes) for safety
        if self.series in ("2400", "2400G"):
            self.write("sour:volt:prot 20")

        self.setWires(two_wire)

        if self.series in ("2400", "2400G"):
            self.write("sens:func 'curr:dc', 'volt:dc'")

        # set what we want reported
        if self.series in ("2400", "2400G"):
            self.write("form:elem time,volt,curr,stat")
        elif self.series in ("2600",):
            buffer_confs = []
            buffer_confs.append("collectsourcevalues = 1")
            buffer_confs.append("appendmode = 0")
            buffer_confs.append("collecttimestamps = 1")
            buffer_confs.append("timestampresolution = 0.0001")
            self.config_buffers(buffer_confs)

        # if self.series in ("2400", "2400G"):
        # from: https://web.archive.org/web/20250109111448/https://download.tek.com/manual/2400S-900-01_K-Sep2011_User.pdf
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

        # if self.series in ("2600",):
        # from: https://web.archive.org/web/20250211012710/https://cores.research.asu.edu/sites/default/files/Keithley%202611A%20Reference%20Manual%202007.pdf
        # status is a 8 bit intiger. bits are these:
        # Bit 0 (0x01): TBD -- Reserved for future use.
        # Bit 1 (0x02): Overtemp -- Over temperature condition.
        # Bit 2 (0x04): AutoRangeMeas -- Measure range was auto ranged.
        # Bit 3 (0x08): AutoRangeSrc -- Source range was auto ranged.
        # Bit 4 (0x10): 4Wire -- 4W (remote) sense mode enabled.
        # Bit 5 (0x20): Rel -- Rel applied to reading.
        # Bit 6 (0x40): Compliance1 -- Source function in compliance.
        # Bit 7 (0x80): Filtered -- Reading was filtered.

        if self.series in ("2400", "2400G"):
            self.write("system:posetup RST")  # system turns on with *RST defaults

        # auto-detect line frequency
        if self.series in ("2400", "2400G"):
            self.write("system:lfrequency:auto ON")
        elif self.series in ("2600",):
            self.write("localnode.autolinefreq = true")

        self.setTerminals(front)

        # check the source(s)
        if self.series in ("2400", "2400G"):
            self.__src = self.query("source:function:mode?")
        elif self.series in ("2600",):
            chans = ["smua"]
            if self.model in ("2602",):
                self.__srcs = ["", ""]
                chans.append("smub")
            for chani, chan in enumerate(chans):
                chansrc = self.query(f"print({chan}.source.func)")
                if chansrc == "0":
                    self.__srcs[chani] = "curr"
                elif chansrc == "1":
                    self.__srcs[chani] = "volt"
            self.__src = self.__srcs[0]

        if self.series in ("2400", "2400G"):
            self.write("system:azero off")  # we'll do this once before every measurement
        elif self.series in ("2600",):
            chans = ["smua"]
            if self.model in ("2602",):
                chans.append("smub")
            for chan in chans:
                self.write(f"{chan}.measure.autozero = {chan}.AUTOZERO_OFF")

        if self.series in ("2400", "2400G"):
            self.write("system:azero:caching:state ON")

            # enable/setup contact check :system:ccheck
            self.opts = self.query("*OPT?")
            if "CONTACT-CHECK" in self.opts.upper():
                self.write("syst:cch 0")  # disable feature
                # self.write("syst:cch:res 50")  # choices are 2, 15 or 50 (50 is default)

        # reset the internal timer
        if self.series in ("2400", "2400G"):
            self.write("syst:time:res")
        elif self.series in ("2600",):
            self.write("timer.reset()")

        # the beeps are annoying
        if self.series in ("2400", "2400G"):
            self.write("syst:beep:stat 0")
        elif self.series in ("2600",):
            self.write("beeper.enable = 0")

        self.lg.debug("k2xxx setup complete.")

    def read(self) -> str:
        if not self.ser:
            raise RuntimeError("smu comms not set up")
        return self.ser.read_until(self.__read_term_bytes).decode().removesuffix(self.__read_term_str)

    def write(self, cmd:str):
        if not self.ser:
            raise RuntimeError("smu comms not set up")
        cmd_bytes = len(cmd)
        self.cts()
        bytes_written = self.ser.write(cmd.encode() + self.__write_term_bytes)
        if bytes_written is None:
            raise ValueError("Write failure.")
        elif cmd_bytes != (bytes_written - self.__write_term_len):
            raise ValueError(f"Write failure: {bytes_written - self.write_term_len} != {cmd_bytes}")

    def query(self, question: str) -> str:
        self.write(question)
        return self.read()

    def opc(self) -> bool:
        """asks the hardware to finish whatever it's doing then send a 1"""
        retries = 5
        ret: bool = False
        opc_val = None
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

    def hardware_reset(self):
        """attempt to stop everything and put the hardware into a known baseline state"""
        try:
            self.opc()
        except:
            pass

        try:
            if self.ser:
                self.ser.send_break()
        except:
            pass

        try:
            self.interrupt()
        except:
            pass

        try:
            if self.ser:
                self.ser.send_break()
        except:
            pass

        # the above breaks can generate DCLs so lets discard those
        try:
            if self.ser:
                self.ser.reset_input_buffer()
        except:
            pass

        try:
            self.write("*RST")  # GPIB defaults
        except:
            pass
        else:
            self.__src = "volt"

        try:
            self.opc()
        except:
            pass

        # identify instrument if we haven't done it yet
        if not self.model:
            self.identify()

        try:
            self.opc()
        except:
            pass

        try:
            self.write("*CLS")  # clear status
        except:
            pass

        try:
            self.opc()
        except:
            pass

        try:
            self.write("*ESE 0")  # disable status events
        except:
            pass

        try:
            self.opc()
        except:
            pass

        try:
            self.write("*SRE 0")  # disable service requests
        except:
            pass

        try:
            self.opc()
        except:
            pass

        if self.model in ["2400", "2450"]:
            try:
                self.write("syst:pres")  # factory defaults
            except:
                pass
            else:
                self.__src = "volt"

            try:
                self.opc()
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
                self.write("trac:cle")  # clear trace/data buffer
            except:
                pass

        try:
            self.opc()
        except:
            pass

        try:
            if self.ser:
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

        self.hard_input_buffer_reset()

        if "socket" in self.address:
            try:
                self.hard_input_buffer_reset(self.ser._socket)
            except Exception as e:
                self.lg.debug("Issue resetting input buffer during disconnect: {e}")

            # use the dead socket port to close the connection from the other side
            self.dead_socket_cleanup(self.__sockethost)

        try:
            if self.ser:
                self.ser.close()
        except Exception as e:
            self.lg.debug("Issue disconnecting: {e}")

        if "socket" in self.address:
            self.socket_cleanup(self.__sockethost, self.__socketport)
            self.dead_socket_cleanup(self.__sockethost)  # use dead socket port to clean up old connections
            self.socket_cleanup(self.__sockethost, self.__socketport)

        if self.ser:
            self.connected = self.ser.is_open
        else:
            self.connected = False

    def setWires(self, two_wire=False):
        self.two_wire = two_wire  # record setting

        if two_wire:
            # four wire mode off
            if self.series in ("2400", "2400G"):
                self.write("syst:rsen 0")
            elif self.series in ("2600",):
                chans = ["smua"]
                if self.model in ("2602",):
                    chans.append("smub")
                for chan in chans:
                    self.write(f"{chan}.sense = {chan}.SENSE_LOCAL")
        else:
            # four wire mode on
            if self.series in ("2400", "2400G"):
                self.write("syst:rsen 1")  # four wire mode off
            elif self.series in ("2600",):
                chans = ["smua"]
                if self.model in ("2602",):
                    chans.append("smub")
                for chan in chans:
                    self.write(f"{chan}.sense = {chan}.SENSE_REMOTE")

    def setTerminals(self, front=False):
        if self.series in ("2400", "2400G"):
            if front:
                self.write("rout:term fron")
            else:
                self.write("rout:term rear")

    def updateSweepStart(self, startVal):
        self.write(f"source:{self.__src}:start {startVal:0.8f}")

    def updateSweepStop(self, stopVal):
        self.write(f"source:{self.__src}:stop {stopVal:0.8f}")

    # sets the source to some value
    def setSource(self, outVal):
        self.write(f"source:{self.__src} {outVal:0.8f}")

    def outOn(self, on=True):
        if on:
            self.write("outp 1")
        else:
            self.write("outp 0")

    def getNPLC(self):
        return float(self.query("sens:curr:nplc?"))

    def setNPLC(self, nplc: float):
        self.nplc_user_set = nplc
        self.write(f"sens:curr:nplc {nplc}")
        self.write(f"sens:volt:nplc {nplc}")
        self.write(f"sens:res:nplc {nplc}")
        if nplc < 1:
            self.write("display:digits 5")
        else:
            self.write("display:digits 7")

    def setupDC(self, sourceVoltage: bool = True, compliance: float = 0.04, setPoint: float = 0.0, senseRange: str = "f", ohms: str | bool = False):
        """setup DC measurement operation
        if senseRange == 'a' the instrument will auto range for both current and voltage measurements
        if senseRange == 'f' then the sense range will follow the compliance setting
        if sourceVoltage == False, we'll have a current source at setPoint amps with max voltage +/- compliance volts
        ohms = True will use the given DC source/sense settings but include a resistance measurement in the output
        ohms = "auto" will override everything and make the output data change to (voltage,current,resistance,time,status)
        """

        if ohms == "auto":
            self.write('sens:func "res"')
            self.write("sens:res:mode auto")
            self.lg.warning("Auto sense resistance mode could result in dangerously high current and/or voltage on the SMU's terminals")
            self.write("sens:res:range:auto on")
            # sm.write('sens:res:range 20E3')
            self.write("format:elements voltage,current,resistance,time,status")
        elif isinstance(ohms, bool):
            if ohms:
                self.write("format:elements voltage,current,resistance,time,status")
                self.write("sens:resistance:mode man")
            else:
                self.write('sens:func:off "resistance"')
                self.write("format:elements voltage,current,time,status")

            if sourceVoltage:
                src = "volt"
                snc = "curr"
            else:
                src = "curr"
                snc = "volt"
            self.__src = src
            self.write(f"source:func {src}")
            self.write(f"source:{src}:mode fixed")
            self.write(f"source:{src} {setPoint:0.8f}")

            self.write("source:delay:auto on")

            if ohms:
                self.write('sens:func "res"')
            else:
                self.write(f'sens:func "{snc}"')
            self.write(f"sens:{snc}:prot {compliance:0.8f}")

            # set the sense range
            if senseRange == "f":
                self.write(f"sens:{snc}:range:auto off")
                self.write(f"sens:{snc}:protection:rsynchronize on")
            elif senseRange == "a":
                self.write(f"sens:{snc}:range:auto on")
            else:
                self.write(f"sens:{snc}:range {senseRange:0.8f}")

            # this again is to make sure the sense range gets updated
            self.write(f"sens:{snc}:protection {compliance:0.8f}")

            # always auto range ohms
            if ohms:
                self.write(f"sens:res:range:auto on")

        self.do_r = ohms
        self.outOn()
        self.write("trigger:count 1")

        self.do_azer()

    def setupSweep(self, sourceVoltage: bool = True, compliance: float = 0.04, nPoints: int = 101, stepDelay: float = -1, start: float = 0.0, end: float = 1.0, senseRange: str = "f"):
        """setup for a sweep operation
        if senseRange == 'a' the instrument will auto range for both current and voltage measurements
        if senseRange == 'f' then the sense range will follow the compliance setting
        if stepDelay < 0 then step delay is on auto (~5ms), otherwise it's set to the value here (in seconds)
        """
        assert self.ser, "smu comms not set up"

        nplc = self.getNPLC()
        ln_freq = 50  # assume 50Hz line freq just because that's safer for timing
        n_types = 2  # we measure both V and I
        adc_conversion_time = (ln_freq * nplc) * n_types
        adc_conversion_time_ms = adc_conversion_time * 1000
        t_overhead_ms = 3  # worst case overhead in SDM cycle (see 2400 manual A-7, page 513)
        sdm_period_baseline = adc_conversion_time_ms + t_overhead_ms

        if sourceVoltage:
            src = "volt"
            snc = "curr"
        else:
            src = "curr"
            snc = "volt"
        self.__src = src
        self.write(f"sour:func {src}")
        self.write(f"sour:{src} {start:0.8f}")

        # seems to do exactly nothing
        # if snc == 'current':
        #  holdoff_delay = 0.005
        #  sm.write(':sense:current:range:holdoff on')
        #  sm.write(':sense:current:range:holdoff {:.6f}'.format(holdoff_delay))
        #  self.opc()  # needed to prevent input buffer overrun with serial comms (should be taken care of by flowcontrol!)

        self.write(f"sens:{snc}:prot {compliance:0.8f}")

        if senseRange == "f":
            self.write(f"sens:{snc}:range:auto 0")
            self.write(f"sens:{snc}:prot:rsyn 1")
        elif senseRange == "a":
            self.write(f"sens:{snc}:range:auto on")
        else:
            self.write(f"sens:{snc}:range {senseRange:0.8f}")

        # this again is to make sure the sense range gets updated
        self.write(f"sens:{snc}:prot {compliance:0.8f}")

        self.outOn()
        self.write(f"sour:{src}:mode sweep")
        self.write("sour:sweep:spacing linear")
        if stepDelay < 0:
            # this just sets delay to 1ms (probably. the actual delay is in table 3-4, page 97, 3-13 of the k2400 manual)
            self.write("sour:delay:auto 1")
            sdm_delay_ms = 3  # worst case
        else:
            self.write("sour:delay:auto 0")
            self.write(f"sour:delay {stepDelay:0.6f}")  # this value is in seconds!
            sdm_delay_ms = stepDelay * 1000

        self.write(f"trigger:count {nPoints}")
        self.write(f"sour:sweep:points {nPoints}")
        self.write(f"sour:{src}:start {start:0.8f}")
        self.write(f"sour:{src}:stop {end:0.8f}")

        # relax the timeout since the above can take a bit longer to process
        self.ser.timeout = 5
        if sourceVoltage:
            self.dV = abs(float(self.query("sour:volt:step?")))
        else:
            self.dI = abs(float(self.query("sour:curr:step?")))
        self.ser.timeout = self.timeout  # restore default timeout
        # sm.write(':source:{:s}:range {:.4f}'.format(src,max(start,end)))
        self.write("sour:sweep:ranging best")
        # sm.write(':sense:{:s}:range:auto off'.format(snc))

        self.do_azer()

        # calculate the expected sweep duration with safety margin
        to_fudge_margin = 1.2  # give the sweep an extra 20 per cent in case our calcs are slightly off
        max_sweep_duration_ms = nPoints * (sdm_delay_ms + sdm_period_baseline) * to_fudge_margin  # [ms]

        # make sure long sweeps don't result in comms timeouts
        max_transport_time_ms = 10000  # [ms] let's asssetupSume no sweep will ever take longer than 10s to transport
        self.ser.timeout = (max_sweep_duration_ms + max_transport_time_ms) / 1000  # [s]

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
        if isinstance(self.do_r, bool) and (not self.do_r):
            pps = 4
        else:
            pps = 5
        vals = []
        self.write("read?")  # trigger measurement
        red = self.read()
        red_nums = [float(x.removesuffix("\x00")) for x in red.split(",")]
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
            stats_string = f"sweep duration={self.last_sweep_time:0.2f}s|mean voltage step={(v_start-v_end)/n_vals*1000:+0.2f}mV|mean sample period={self.last_sweep_time/n_vals*1000:0.0f}ms|mean sweep rate={(v_start-v_end)/self.last_sweep_time:+0.3f}V/s"
            if self.print_sweep_deets:
                self.lg.log(29, stats_string)
            else:
                self.lg.debug(stats_string)
            # reset comms timeout to default value after sweep
            if self.ser:
                self.ser.timeout = self.timeout

        # update the status byte
        self.status = vals[-1][-1]
        return vals

    def measure_until(self, t_dwell: float = float("Infinity"), n_measurements=float("Infinity"), cb=lambda x: None) -> list[tuple[float, float, float, int]] | list[tuple[float, float, float, float, int]]:
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
        while (i < n_measurements) and (time.time() < t_end) and (not self.killer.is_set()):
            i = i + 1
            measurement = self.measure()
            q.append(measurement[0])
            cb(measurement)
        if self.killer.is_set():
            self.lg.debug("Killed by killer")
        return q

    def enable_cc_mode(self, value: bool = True):
        """setup contact check mode"""
        if self.cc_mode == "internal":
            # note that this also checks the GUARD-SENSE connections, short those manually if not in use
            if self.query("syst:rsen?") == "1":
                if "CONTACT-CHECK" in self.opts.upper():
                    if value:
                        self.outOn(on=False)
                        self.write("outp:smod guar")
                        self.write("system:cchech ON")
                        self.write("sense:voltage:nplc 0.1")
                        self.write("sense:current:nplc 0.1")
                        self.write("sense:resistance:nplc 0.1")
                        # setup I=0 voltage measurement
                        self.setupDC(sourceVoltage=False, compliance=3, setPoint=0, senseRange="f", ohms=False)
                    else:
                        self.write("output:smode himpedance")
                        self.outOn(on=False)
                        self.write(f"sense:voltage:nplc {self.nplc_user_set}")
                        self.write(f"sense:current:nplc {self.nplc_user_set}")
                        self.write(f"sense:resistance:nplc {self.nplc_user_set}")
                        self.write("system:cchech OFF")
                else:
                    self.lg.debug("Contact check option not installed")
            else:
                self.lg.debug("Contact check function requires 4-wire mode")
        elif self.cc_mode == "external":
            sense_current = 0.001  # A
            compliance_voltage = 3  # V
            self.outOn(on=False)
            if self.query("outp?") == "0":  # check if that worked
                if value:
                    self.write("syst:rsen 0")  # four wire mode off
                    self.write("sense:voltage:nplc 0.1")
                    self.write("sense:current:nplc 0.1")
                    self.write("sense:resistance:nplc 0.1")
                    self.set_do(13)  # HI check
                    time.sleep(self.t_relay_bounce)
                    self.setupDC(sourceVoltage=False, compliance=compliance_voltage, setPoint=sense_current, senseRange="f", ohms=True)
                    self.last_lo = False  # mark as set up for hi side checking
                else:
                    self.setWires(self.two_wire)  # restore previous 2/4 wire setting
                    self.write(f"sense:voltage:nplc {self.nplc_user_set}")  # restore previous nplc setting
                    self.write(f"sense:current:nplc {self.nplc_user_set}")
                    self.write(f"sense:resistance:nplc {self.nplc_user_set}")
                    self.setupDC(sourceVoltage=False, compliance=compliance_voltage, setPoint=0.0, senseRange="f", ohms=False)
                    self.outOn(on=False)
                    if self.query("outp?") == "0":  # check if that worked
                        self.set_do(15)  # normal operation
                        time.sleep(self.t_relay_bounce)
                        self.last_lo = None  # mark as unsetup for for any contact checking
        else:
            self.lg.warning("The contact check feature is not configured.")

    def do_contact_check(self, lo_side: bool = False) -> tuple[bool, float]:
        """
        call enable_cc_mode(True) before calling this
        and enable_cc_mode(False) after you're done checking contacts
        attempts to turn on the output and trigger a measurement.
        tests if the output remains on after that. if so, the contact check passed
        True for contacted. always true if the sourcemeter hardware does not support this feature
        """
        # cc_mode can be "none", "external" or "internal" (internal is for -c model 24XXs only)
        good_contact = False
        r_val = 1000000.0
        if self.cc_mode == "internal":
            self.outOn()  # try to turn on the output
            if self.query(":output?") == "1":  # check if that worked
                self.write("init")
                time.sleep(0.1)  # TODO: figure out a better way to do this. mysterious dealys = bad
                if self.query(":output?") == "1":
                    good_contact = True  # if INIT didn't trip the output off, then we're connected
        elif self.cc_mode == "external":
            # TODO: add a potential check
            if lo_side is None:
                self.lg.debug("Contact check has not been set up.")
            else:
                if ((lo_side) and (not self.last_lo)) or ((not lo_side) and (self.last_lo)):  # we're not set up for the right checking side
                    # we need to reconfigure the relays. do that with the output off
                    self.outOn(on=False)
                    if self.query("outp?") == "0":  # check if that worked
                        if lo_side:
                            self.set_do(14)  # LO check
                            self.last_lo = True  # mark as set up for lo side checking
                        if not lo_side:
                            self.set_do(13)  # HI check
                            self.last_lo = False  # mark as set up for high side checking
                        time.sleep(self.t_relay_bounce)
                        self.outOn()
                if self.query("outp?") == "1":  # check that the output is on
                    m = self.measure()[0]
                    if len(m) == 5:
                        r_val = m[2]
                        status = int(m[4])
                        in_compliance = (1 << 3) & status  # check compliance bit (3) in status word
                        if not in_compliance:
                            if abs(r_val) < self.threshold_ohm:
                                good_contact = True
                        #         self.lg.debug(f"CC resistance in  of bounds: abs({r_val}Ω) <  {self.threshold_ohm}Ω")
                        #     else:
                        #         self.lg.debug(f"CC resistance out of bounds: abs({r_val}Ω) >= {self.threshold_ohm}Ω")
                        # else:
                        #     self.lg.debug(f"CC compliance failure: V={m[0]}V, I={m[1]}A")
        elif self.cc_mode == "none":
            good_contact = True
        return (good_contact, r_val)

    def set_do(self, value: int):
        """sets digital output"""
        self.write(f"sour2:ttl {value}")
        readback = self.query(f"sour2:ttl:act?")
        if f"{value}" != readback:
            self.lg.debug("digital output readback failure: {value} != {readback}")


if __name__ == "__main__":
    rfc_to = 6  # rfc2217/telnet timeout
    ser_to = 5  # serial object timeout
    addr = f"rfc2217://adapter:9001?timeout={rfc_to}&logging=debug"  # for debugging
    addr = f"rfc2217://adapter:9001?timeout={rfc_to}"
    init_kwargs = {}
    init_kwargs["two_wire"] = False
    init_kwargs["write_term"] = "\r\n"
    init_kwargs["read_term"] = "\n"
    init_kwargs["baudrate"] = 115200
    init_kwargs["bytesize"] = serial.EIGHTBITS
    init_kwargs["parity"] = serial.PARITY_ODD
    init_kwargs["stopbits"] = serial.STOPBITS_ONE
    init_kwargs["timeout"] = ser_to
    init_kwargs["xonxoff"] = False
    init_kwargs["rtscts"] = True
    init_kwargs["dsrdtr"] = False

    with k2xxx(addr, **init_kwargs) as k:
        for i in range(1000):
            print(f'{i}:{k.query("*IDN?")}')
