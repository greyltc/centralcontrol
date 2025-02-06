from collections import OrderedDict
import errno
import inspect
import time
import serial
from threading import Event as tEvent
from multiprocessing.synchronize import Event as mEvent
import pathlib
import re
import socket

import logging

# workaround for when centralcontrol not in current environment
try:
    from centralcontrol.logstuff import get_logger
except ImportError:

    def get_logger(name: str, level: int):
        lg = logging.getLogger()
        lg.setLevel(level)

        log_format = logging.Formatter(
            "%(asctime)s|%(name)s|%(levelname)s|%(filename)s:%(lineno)d|%(funcName)s|%(message)s"
        )

        # console logger
        ch = logging.StreamHandler()
        ch.setFormatter(log_format)
        lg.addHandler(ch)

        # file logger
        LOG_FOLDER = pathlib.Path("data").joinpath("log")
        if not LOG_FOLDER.exists():
            LOG_FOLDER.mkdir(parents=True)
        fh = logging.FileHandler(LOG_FOLDER.joinpath(f"{int(time.time())}.log"))
        fh.setFormatter(log_format)
        lg.addHandler(fh)

        return lg


# LOG_LEVEL = logging.INFO
LOG_LEVEL = logging.DEBUG

RX_TERMCHAR = "\r"
TX_TERMCHAR = "\r"

ERR_QUERY = "syst:err:all?"
NO_ERR_MSG = "+000, No Error"
ERR_REGEX = re.compile(r"^([-+]\d{3},[^;]+)(;[-+]\d{3},[^;]+)*$")

CONNECT_TIMEOUT = 10
RW_TIMEOUT = 3  # read and write timeout
RESET_TIMEOUT = 0.5


class AmSmu(object):
    """
    Interface for Ark Metrica sourcemeter
    """

    expect_in_idn = "Ark Metrica"
    quiet = False
    idn = ""
    opts = ""
    status = 0
    nplc_user_set = 1.0
    last_sweep_time: float = 0.0
    readyForAction = False
    four88point1 = False
    print_sweep_deets = (
        False  # false uses debug logging level, true logs sweep stats at info level
    )
    _write_term_str = TX_TERMCHAR
    _read_term_str = RX_TERMCHAR
    ser: serial.Serial | None = None
    timeout: float | None = None  # default comms timeout
    do_r: str | bool = False  # include resistance in measurement
    t_relay_bounce = 0.05  # number of seconds to wait to ensure the contact check relays have stopped bouncing
    last_lo = None  # we're not set up for contact checking
    cc_mode = "none"  # contact check mode
    is_2450: bool | None = None
    killer: tEvent | mEvent
    _address: str = ""
    threshold_ohm = 33.3  # resistance values below this give passing tests

    read_timeout_flag: bool = False

    def __init__(
        self,
        address: str,
        front: bool = True,
        two_wire: bool = True,
        quiet: bool = False,
        killer: tEvent | mEvent = tEvent(),
        print_sweep_deets: bool = False,
        cc_mode: str = "none",
        **kwargs,
    ):
        """just set class variables here"""

        # setup logging
        self.lg = get_logger(".".join([__name__, type(self).__name__]), LOG_LEVEL)

        self.killer = killer
        self.quiet = quiet
        self._address = address
        self.front = front
        self._two_wire = two_wire
        self.print_sweep_deets = print_sweep_deets
        self.write_term = bytes([ord(x) for x in self._write_term_str])
        self.read_term = bytes([ord(x) for x in self._read_term_str])
        self.write_term_len = len(self.write_term)
        self.read_term_len = len(self.read_term)
        self.cc_mode = cc_mode
        self.remaining_init_kwargs = kwargs

        # line frequency gets applied during self.setup() call
        if "line_frequency" in self.remaining_init_kwargs:
            self._line_frequency = self.remaining_init_kwargs["line_frequency"]
        else:
            self.lg.warning("Assuming 50Hz mains power frequency")
            self._line_frequency = 50

        # write-only parameter commands are always followed by an error check to
        # validate input and limit the rate of commands into the controller's read
        # buffer to prevent overflow
        # this flag is used to indicate whether a write has been called by the query
        # method, and therefore the error check should be disabled
        self._check_error = True

        # hold latest error message
        self._err = ""

        # container for state of all smu parameters
        # DANGER! DO NOT EDIT MANUALLY!
        self.__state = OrderedDict()

        self.lg.debug("AmSmu initialized.")

    def __enter__(self) -> "AmSmu":
        """so that the smu can enter a context"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        """so that the smu can leave a context cleanly"""
        self.disconnect()
        return False

    def __update_state(self):
        """Add a method and its arguments to the _state dictionary for recall later.

        This method should be called at the end of every property setter method that
        relates to the SMU state, e.g. set voltage, source function etc. It stores the
        property name and the property's arguments as a key, value pair in the _state
        dictionary. The dictionary can later be called to replay the setter calls and
        recover the last known state if needed.

        This method should never be called outside of the class. The double underscore
        invokes name mangling to make this difficult.
        """
        # Get the calling frame
        frame = inspect.currentframe()
        caller_frame = frame.f_back

        # Get the method object and its signature
        method_name = caller_frame.f_code.co_name

        # Attempt to retrieve the attribute
        attr = getattr(self.__class__, method_name, None)

        # Check if the attribute is a property or a method
        if isinstance(attr, property):
            # It's a property; use the setter for the signature
            method = attr.fset  # fset is the setter method of the property
        elif callable(attr):
            # It's a regular method
            method = attr
        else:
            raise TypeError(f"{method_name!r} is neither a property nor a method.")

        sig = inspect.signature(method)

        # Get local variables
        local_vars = dict(caller_frame.f_locals)

        # Bind the arguments to the method's signature, excluding local variables not
        # in the method's signature
        bound_args = sig.bind_partial(**local_vars)

        # If the method is already in the ordered state dictionary, remove it reinsert
        # so it can be reinserted at the end
        if method in self.__state:
            del self.__state[method]

        # Store the callable in the _state dictionary as the method and its bound
        # arguments
        self.__state[method] = bound_args

    def _restore_state(self):
        """Restore the SMU state using the _state dictionary.

        This should only be called to restore state in the unlikely event of an SMU
        watchdog timeout.
        """
        # iterate over a copy to avoid issues caused by mutating self.__state when
        # methods get called that modify self.__state
        state_items = list(self.__state.items())
        for method, bound_args in state_items:
            method(*bound_args.args, **bound_args.kwargs)
            self.lg.debug(f"Restored: {method}, {bound_args.args}, {bound_args.kwargs}")

    @property
    def address(self) -> str:
        return self._address

    @address.setter
    def address(self, address: str):
        if self.connected:
            raise ValueError("Address cannot be updated whilst socket is open.")

        if not re.match(r"^socket://([\w.-]+):(\d{1,5})$", address):
            raise ValueError(
                r"Invalid address string for PySerial socket. Address must be formatted as 'socket://{host}:{port}'"
            )

        self._address = address

    @property
    def line_frequency(self) -> int:
        # line frequency gets set during self.setup(), which is called during
        # self.connect() so instrument response should always correspond to
        # self._line_frequency after connection has been made
        return int(self.query("syst:lfr?"))

    @line_frequency.setter
    def line_frequency(self, frequency: int):
        if frequency not in [50, 60]:
            raise ValueError(
                f"Invalid line frequency: {frequency}. Must be '50' or '60'."
            )

        self.write(f"syst:lfr {frequency}")
        self._line_frequency = frequency

    @property
    def connected(self) -> bool:
        if self.ser:
            return self.ser.is_open
        else:
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

    @property
    def sweep_step_voltage(self) -> float:
        return abs(float(self.query("sour:volt:step?")))

    @property
    def sweep_step_current(self) -> float:
        return abs(float(self.query("sour:curr:step?")))

    @property
    def status_byte(self) -> int:
        return int(self.query("*STB?"))

    # --- smu state ---
    @property
    def source_function(self) -> str:
        return self.query("sour:func?")

    @source_function.setter
    def source_function(self, function: str):
        if function not in ["volt", "curr"]:
            raise ValueError(
                r"Invalid source function. Source function must be either 'curr' or 'volt'."
            )
        self.write(f"sour:func {function}")

        self.__update_state()

    @property
    def output_enabled(self) -> bool:
        return bool(int(self.query("outp?")))

    @output_enabled.setter
    def output_enabled(self, enabled: bool):
        if enabled:
            self.write("outp 1")
        else:
            self.write("outp 0")

        self.__update_state()

    @property
    def nplc(self) -> float:
        return float(self.query("sens:curr:nplc?"))

    @nplc.setter
    def nplc(self, nplc: float):
        self.write(f"sens:curr:nplc {nplc:0.6f}")

        # for compatibility with central control
        self.nplc_user_set = nplc

        self.__update_state()

    @property
    def settling_delay(self) -> float:
        """Settling delay in s."""
        return float(self.query("sour:del?"))

    @settling_delay.setter
    def settling_delay(self, delay: float):
        """Settling delay in s."""
        self.write(f"sour:del {delay:0.6f}")

        self.__update_state()

    @property
    def auto_settling_delay(self) -> bool:
        return bool(int(self.query("sour:del:auto?")))

    @auto_settling_delay.setter
    def auto_settling_delay(self, auto: bool):
        if auto:
            self.write("sour:del:auto 1")
        else:
            self.write("sour:del:auto 0")

        self.__update_state()

    @property
    def source_voltage(self) -> float:
        return float(self.query("sour:volt?"))

    @source_voltage.setter
    def source_voltage(self, voltage: float):
        self.write(f"sour:volt {voltage:0.8f}")

        self.__update_state()

    @property
    def source_current(self) -> float:
        return float(self.query("sour:curr?"))

    @source_current.setter
    def source_current(self, current: float):
        self.write(f"sour:curr {current:0.8f}")

        self.__update_state()

    @property
    def compliance_voltage(self) -> float:
        return float(self.query("sens:volt:prot?"))

    @compliance_voltage.setter
    def compliance_voltage(self, voltage: float):
        self.write(f"sens:volt:prot {voltage:0.8f}")

        self.__update_state()

    @property
    def compliance_current(self) -> float:
        return float(self.query("sens:curr:prot?"))

    @compliance_current.setter
    def compliance_current(self, current: float):
        self.write(f"sens:curr:prot {current:0.8f}")

        self.__update_state()

    @property
    def sweep_start_voltage(self) -> float:
        return float(self.query("sour:volt:start?"))

    @sweep_start_voltage.setter
    def sweep_start_voltage(self, voltage: float):
        self.write(f"sour:volt:start {voltage:0.8f}")

        self.__update_state()

    @property
    def sweep_start_current(self) -> float:
        return float(self.query("sour:curr:start?"))

    @sweep_start_current.setter
    def sweep_start_current(self, current: float):
        self.write(f"sour:curr:start {current:0.8f}")

        self.__update_state()

    @property
    def sweep_stop_voltage(self) -> float:
        return float(self.query("sour:volt:stop?"))

    @sweep_stop_voltage.setter
    def sweep_stop_voltage(self, voltage: float):
        self.write(f"sour:volt:stop {voltage:0.8f}")

        self.__update_state()

    @property
    def sweep_stop_current(self) -> float:
        return float(self.query("sour:curr:stop?"))

    @sweep_stop_current.setter
    def sweep_stop_current(self, current: float):
        self.write(f"sour:curr:stop {current:0.8f}")

        self.__update_state()

    @property
    def sweep_points(self) -> int:
        return int(self.query("sour:swe:poin?"))

    @sweep_points.setter
    def sweep_points(self, points: int):
        self.write(f"sour:swe:poin {points}")

        self.__update_state()

    @property
    def sweep_spacing(self) -> str:
        return self.query("sour:swe:spac?")

    @sweep_spacing.setter
    def sweep_spacing(self, spacing: str):
        if spacing not in ["lin", "log"]:
            raise ValueError(
                r"Invalid sweep spacing. Sweep spacing must be either 'lin' or 'log'."
            )
        self.write(f"sour:swe:spac {spacing}")

        self.__update_state()

    @property
    def source_voltage_mode(self) -> str:
        return self.query("sour:volt:mode?")

    @source_voltage_mode.setter
    def source_voltage_mode(self, mode: str):
        if mode not in ["fix", "swe", "list"]:
            raise ValueError(
                r"Invalid source mode. Source mode must be either 'fix', 'swe', or 'list'."
            )
        self.write(f"sour:volt:mode {mode}")

        self.__update_state()

    @property
    def source_current_mode(self) -> str:
        return self.query("sour:curr:mode?")

    @source_current_mode.setter
    def source_current_mode(self, mode: str):
        if mode not in ["fix", "swe", "list"]:
            raise ValueError(
                r"Invalid source mode. Source mode must be either 'fix', 'swe', or 'list'."
            )
        self.write(f"sour:curr:mode {mode}")

        self.__update_state()

    @property
    def remote_sense(self) -> bool:
        if self.connected:
            return bool(int(self.query("syst:rsen?")))
        else:
            # for compatibility with central control
            return not self._two_wire

    @remote_sense.setter
    def remote_sense(self, remote_sense: bool):
        if remote_sense:
            self.write("syst:rsen 1")
        else:
            self.write("syst:rsen 0")

        # for compatibility with central control
        self._two_wire = not remote_sense

        self.__update_state()

    # --- alias's for compatibility with central control ---
    @property
    def src(self) -> str:
        return self.source_function

    @src.setter
    def src(self, function: str):
        self.source_function = function

    @property
    def two_wire(self) -> bool:
        return not self.remote_sense

    @two_wire.setter
    def two_wire(self, two_wire: bool):
        self.remote_sense = not two_wire

    def _check_wdt_reset_bit(self, restore_state: bool = False):
        """Check if WDT bit of status byte is set.

        Optionally, restore the saved state if it is.

        Parameters
        ----------
        restore_state : bool
            If `True`, restore saved state if WDT bit is set.
        """
        wdt_bit_set = self.status_byte & (1 << 0) != 0

        if wdt_bit_set:
            self.lg.warning("WDT bit set")
            self._clear_wdt_bit()

            if restore_state:
                self._restore_state()
                self.lg.warning("State restored")

    def _clear_wdt_bit(self):
        self.write("syst:wdt:cle")

    def dead_socket_cleanup(self, host):
        """attempts dead socket cleanup on a 2450 via the dead socket port"""
        pass

    def socket_cleanup(self, host, port):
        """ensure a the host/port combo is clean and closed"""
        pass

    def hard_input_buffer_reset(self, s: socket.socket | None = None) -> bool:
        """brute force input buffer discard with failure check.

        Parameters
        ----------
        s : socket.socket | None
            Socket object. If None, use self.ser object.
        """
        try:
            if isinstance(s, socket.socket):
                oto = s.gettimeout()
                s.settimeout(RESET_TIMEOUT)

                # chuck anything that was sent to us
                while len(s.recv(16)) != 0:
                    pass
            elif self.ser:
                oto = self.ser.timeout  # save timeout value
                self.ser.timeout = RESET_TIMEOUT

                self._clear_input_buffer()
            else:
                return False
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

    def _clear_input_buffer(self):
        """Clear the input buffer."""
        buffer = ""
        while self.ser.in_waiting:
            buffer += self.ser.read(1).decode()

        self.lg.debug(f"Cleared buffer contents: {buffer}")

    def connect(self):
        """attempt to connect to hardware and initialize it"""
        self._low_level_connect()

        self.hardware_reset()

        self.setup(self.front, self._two_wire)

        self._check_wdt_reset_bit(restore_state=True)

        return 0

    def _low_level_connect(self):
        """Connect to socket."""
        if self.ser:
            self.ser.close()
            time.sleep(1)

        self.host, self.port = self.address.removeprefix("socket://").split(":", 1)

        remaining_connection_retries = 5
        while remaining_connection_retries > 0:
            try:
                # NOTE: this sets pyserial's timeout, which has nothing to do with the
                # underlying socket timeout; the socket retains its the default setting
                self.ser = serial.serial_for_url(self.address, timeout=CONNECT_TIMEOUT)
                time.sleep(0.5)  # TODO: remove this hack  (but it adds stability)
                self.ser.timeout = RW_TIMEOUT  # set default read and write timeout
            except (
                ConnectionRefusedError,
                serial.serialutil.SerialException,
                serial.serialutil.PortNotOpenError,
            ):
                self.lg.debug("Connection attempt refused")

                # Close the previous connection attempt
                if self.ser:
                    self.ser.close()

                # controller may need longer to re-open socket after it was last closed
                # wait and try again
                time.sleep(5)

            # check for a "ghost connection"
            # this block effectively passes if the connection attempt was refused
            if self.hard_input_buffer_reset():
                # not a ghost connection, exit connection retry loop
                break
            else:
                # ghost connection!
                self.disconnect()

            remaining_connection_retries -= 1
            self.lg.debug(
                "Connection retries remaining: %d", remaining_connection_retries
            )
        else:
            raise ValueError(
                f"Connection retries exhausted while connecting to {self.address}"
            )

        self.timeout = self.ser.timeout
        self.lg.debug(f"Socket timeout: {self.ser.timeout} s")

        self.lg.debug("AmSmu connected.")

    def setup(self, front=True, two_wire=False):
        """does baseline configuration in prep for data collection"""
        # ask the device to identify its self
        self.idn = self.query("*IDN?")

        self.is_2450 = False

        # apply line frequency read from constructor kwargs
        self.line_frequency = self._line_frequency

        # set wrie configuration ready for measurment
        self.remote_sense = not two_wire

        self.lg.debug("AmSmu setup complete.")

    def opc(self) -> bool:
        return True

    def write(self, cmd: str):
        if not self.ser:
            raise ValueError("SMU communications not initialised")

        cmd_bytes = len(cmd)

        write_retries = 3
        while write_retries > 0:
            try:
                bytes_written = self.ser.write(cmd.encode() + self.write_term)
                if cmd_bytes != (bytes_written - self.write_term_len):
                    raise ValueError(
                        f"Write failure, {bytes_written - self.write_term_len} != {cmd_bytes}."
                    )
                break
            except socket.error as e:
                if e.errno == errno.ECONNRESET:
                    self.lg.error("Connection reset by peer on write.")
                else:
                    if e.errno is not None:
                        self.lg.error(f"Socket error occurred on read: [{e.errno}] {errno.errorcode.get(e.errno, "UNKNOWN_ERROR")} ({e}).")
                    else:
                        self.lg.error(f"Unknown Socket error occurred on read: {e}.")
            except ValueError:
                # re-raise commands bytes error
                raise
            except Exception as e:
                self.lg.error("Error occurred on write: %s.", str(e))

            # any error that gets this far should be resolved by reconnecting and
            # retrying
            self._low_level_connect()

            write_retries -= 1
        else:
            raise IOError("Write operation exceeded maximum retries.")

        if not cmd.endswith("?"):
            # write wasn't a query so check for write errors
            self._query_error()

    def query(self, question: str) -> str:
        """Write a question and read the response.

        Parameters
        ----------
        question : str
            Question to ask the device.

        Returns
        -------
        response : str
            Response to the question.
        """
        query_retries = 3
        while query_retries > 0:
            self.write(question)

            try:
                response = self.read()

                if response != "":
                    break
                else:
                    raise ValueError("Read returned an empty string")
            except (socket.timeout, ValueError) as e:
                self.lg.error(f"{e}")

                # NOTE: pyserial returns an empty string on read_until timeouts when
                # using sockets
                # check if timeout occured because request command was invalid (in
                # which case, stop and raise the error) or if it was a comms timeout
                # (in which case, try dummy query to force read).
                # the error query is never invalid so just reconnect and retry.
                if question != ERR_QUERY:
                    try:
                        self.write(ERR_QUERY)
                        err = self.read()

                        if err != "" and not ERR_REGEX.match(err):
                            # response to error query wasn't an error message so was
                            # probably the stuck previous message
                            response = err

                            # input buffer now has the dummy error response so clear it
                            dummy_err = self.read()
                            self.lg.debug(f"Dummy error contents: {dummy_err}")

                            # give controller a bit more time to send anything else
                            # and try to clear the buffer
                            time.sleep(0.5)
                            self._clear_input_buffer()

                            break
                        elif err != NO_ERR_MSG and ERR_REGEX.match(err):
                            # received real error msg so question was probably invalid
                            raise ValueError(err)
                        else:
                            self.lg.debug(
                                "Read timeout validation returned unexpected str: %s",
                                err,
                            )
                    except Exception as e:
                        self.lg.error(
                            "Error occurred during read timeout validation: %s.", str(e)
                        )
            except socket.error as e:
                if e.errno == errno.ECONNRESET:
                    self.lg.error("Connection reset by peer on read.")
                else:
                    if e.errno is not None:
                        self.lg.error(f"Socket error occurred on read: [{e.errno}] {errno.errorcode.get(e.errno, "UNKNOWN_ERROR")} ({e}).")
                    else:
                        self.lg.error(f"Unknown Socket error occurred on read: {e}.")
            except Exception as e:
                self.lg.error("Error occurred on read: %s.", str(e))

            # any error that gets this far should be resolved by reconnecting and
            # retrying
            self._low_level_connect()

            query_retries -= 1
        else:
            raise IOError(f"Query exceeded maximum retries: '{question}'.")

        return response

    def read(self) -> str:
        if not self.ser:
            raise ValueError("SMU communications not initialised.")

        return (
            self.ser.read_until(self.read_term)
            .decode()
            .removesuffix(self.read_term_str)
        )

    def _query_error(self, ignore: bool = False):
        """Read the SMU error buffer.

        Parameters
        ----------
        ignore : bool
            If True, ignore the error returned to clear the buffer. If False, raise
            the error.
        """
        self._err = self.query(ERR_QUERY)

        if self._err != NO_ERR_MSG and not ignore and ERR_REGEX.match(self._err):
            # valid error message, not ignoring it, but showing an error
            raise ValueError(self._err)
        elif not ERR_REGEX.match(self._err):
            self.lg.debug(f"invalid error message: {self._err}")
            raise ValueError(self._err)

    def hardware_reset(self):
        """attempt to stop everything and put the hardware into a known baseline state"""
        self._query_error(ignore=True)  # clear controller error buffer
        self.reset(hard=False)

    def reset(self, hard: bool = False):
        """Reset SMU channel.

        Parameters
        ----------
        hard : bool
            If `False`, perform a software reset. If `True`, force the SMU PCB to power
            cycle. The power cycle requires a 5s delay before the SMU channel can
            respond to commands again.
        """
        if hard:
            self.write("*RST")
            time.sleep(6)  # wait for smu channel to reset
        else:
            self.write("syst:pres")

    def disconnect(self):
        """do our best to close down and clean up the instrument"""
        try:
            self.hard_input_buffer_reset(self.ser._socket)
        except Exception as e:
            self.lg.debug(f"Issue resetting input buffer during disconnect: {e}")

        try:
            if self.ser:
                self.ser.close()
        except Exception as e:
            self.lg.debug(f"Issue disconnecting: {e}")

    def setWires(self, two_wire=False):
        self.remote_sense = not two_wire

    def setTerminals(self, front=False):
        pass

    def updateSweepStart(self, startVal):
        if self.source_function == "volt":
            self.sweep_start_voltage = startVal
        else:
            self.sweep_start_current = startVal

    def updateSweepStop(self, stopVal):
        if self.source_function == "volt":
            self.sweep_stop_voltage = stopVal
        else:
            self.sweep_stop_current = stopVal

    # sets the source to some value
    def setSource(self, outVal):
        if self.source_function == "volt":
            self.source_voltage = outVal
        else:
            self.source_current = outVal

    def outOn(self, on=True):
        self.output_enabled = on

    def getNPLC(self):
        return self.nplc

    def setNPLC(self, nplc: float):
        self.nplc = nplc

    def setupDC(
        self,
        sourceVoltage: bool = True,
        compliance: float = 0.04,
        setPoint: float = 0.0,
        senseRange: str = "f",
        ohms: str | bool = False,
    ):
        """setup DC measurement operation
        if senseRange == 'a' the instrument will auto range for both current and voltage measurements
        if senseRange == 'f' then the sense range will follow the compliance setting
        if sourceVoltage == False, we'll have a current source at setPoint amps with max voltage +/- compliance volts
        ohms = True will use the given DC source/sense settings but include a resistance measurement in the output
        ohms = "auto" will override everything and make the output data change to (voltage,current,resistance,time,status)
        """
        if sourceVoltage:
            src = "volt"
            snc = "curr"

            self.compliance_current = abs(compliance)
            self.source_function = src
            self.source_voltage_mode = "fix"
            self.source_voltage = setPoint
        else:
            src = "curr"
            snc = "volt"

            self.compliance_voltage = abs(compliance)
            self.source_function = src
            self.source_current_mode = "fix"
            self.source_current = setPoint

        self.auto_settling_delay = True

        if senseRange == "f":
            pass
        elif senseRange == "a":
            pass
        else:
            raise NotImplementedError("Range setting not supported.")

        self.do_r = ohms

        if not (sourceVoltage) and setPoint == 0:
            # measuring voltage while sourcing 0A is best achieved with output disabled
            self.output_enabled = False
        else:
            self.output_enabled = True

    def setupSweep(
        self,
        sourceVoltage: bool = True,
        compliance: float = 0.04,
        nPoints: int = 101,
        stepDelay: float = -1,
        start: float = 0.0,
        end: float = 1.0,
        senseRange: str = "f",
    ):
        """setup for a sweep operation
        if senseRange == 'a' the instrument will auto range for both current and voltage measurements
        if senseRange == 'f' then the sense range will follow the compliance setting
        if stepDelay < 0 then step delay is on auto (~5ms), otherwise it's set to the value here (in seconds)
        """
        nplc = self.getNPLC()
        ln_freq = 50  # assume 50Hz line freq just because that's safer for timing
        n_types = 1  # both V and I are measured simulataneously
        adc_conversion_time = (ln_freq * nplc) * n_types
        adc_conversion_time_ms = adc_conversion_time * 1000
        # worst case overhead in SDM cycle (see 2400 manual A-7, page 513)
        t_overhead_ms = 3
        sdm_period_baseline = adc_conversion_time_ms + t_overhead_ms

        if sourceVoltage:
            src = "volt"
            snc = "curr"

            self.compliance_current = abs(compliance)
            self.source_function = src
            self.source_voltage = start

            self.source_voltage_mode = "swe"
            self.sweep_start_voltage = start
            self.sweep_stop_voltage = end
        else:
            src = "curr"
            snc = "volt"

            self.compliance_voltage = abs(compliance)
            self.source_function = src
            self.source_current = start

            self.source_current_mode = "swe"
            self.sweep_start_current = start
            self.sweep_stop_current = end

        self.sweep_spacing = "lin"
        self.sweep_points = nPoints

        if senseRange == "f":
            pass
        elif senseRange == "a":
            pass
        else:
            raise NotImplementedError("Range setting not supported.")

        if stepDelay < 0:
            # this just sets delay to 1ms
            self.auto_settling_delay = True
            sdm_delay_ms = 3  # worst case
        else:
            self.auto_settling_delay = False
            self.settling_delay = stepDelay
            sdm_delay_ms = stepDelay * 1000

        if sourceVoltage:
            self.dV = self.sweep_step_voltage
        else:
            self.dI = self.sweep_step_current

        # calculate the expected sweep duration with safety margin
        to_fudge_margin = 1.2  # give the sweep an extra 20 per cent in case our calcs are slightly off
        max_sweep_duration_ms = (
            nPoints * (sdm_delay_ms + sdm_period_baseline) * to_fudge_margin
        )

        # make sure long sweeps don't result in comms timeouts
        max_transport_time_ms = 10000  # [ms] let's assume no sweep will ever take longer than 10s to transport
        self.ser.timeout = (max_sweep_duration_ms + max_transport_time_ms) / 1000  # [s]

        self.output_enabled = True

    def do_azer(self):
        """parform autozero routine"""
        pass

    def arm(self):
        """arms trigger"""
        pass

    def trigger(self):
        """performs trigger event"""
        pass

    def measure(
        self, nPoints: int = 1
    ) -> (
        list[tuple[float, float, float, int]]
        | list[tuple[float, float, float, float, int]]
    ):
        """Makes a measurement and returns the result
        returns a list of measurements
        a "measurement" is a tuple of length 4: voltage,current,time,status (or length 5: voltage,current,resistance,time,status if dc setup was done in ohms mode)
        for a prior DC setup, the list will be 1 long.
        for a prior sweep setup, the list returned will be n sweep points long
        """
        # if wdt has occured since last check, restore state
        self._check_wdt_reset_bit(restore_state=True)

        # figure out how many points per sample we expect
        if isinstance(self.do_r, bool) and (not self.do_r):
            pps = 4
        else:
            pps = 5
        vals = []

        red = self.query("read?")
        red_nums = red.split(",")
        for i in range(0, len(red_nums), 4):
            if pps == 5:
                line = (
                    float(red_nums[i]),
                    float(red_nums[i + 1]),
                    float(red_nums[i]) / float(red_nums[i + 1]),
                    float(red_nums[i + 2]) / 1000,  # convert t from ms to s
                    int(red_nums[i + 3]),
                )
            else:
                line = (
                    float(red_nums[i]),
                    float(red_nums[i + 1]),
                    float(red_nums[i + 2]) / 1000,  # convert t from ms to s
                    int(red_nums[i + 3]),
                )
            vals.append(line)

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

    def measure_until(
        self,
        t_dwell: float = float("Infinity"),
        n_measurements=float("Infinity"),
        cb=lambda x: None,
    ) -> (
        list[tuple[float, float, float, int]]
        | list[tuple[float, float, float, float, int]]
    ):
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

        while (
            (i < n_measurements)
            and (time.time() < t_end)
            and (not self.killer.is_set())
        ):
            i = i + 1
            measurement = self.measure()
            q.append(measurement[0])
            cb(measurement)
        if self.killer.is_set():
            self.lg.debug("Killed by killer")
        return q

    def enable_cc_mode(self, value: bool = True):
        """setup contact check mode"""
        #self.lg.warning("The contact check feature is not supported.")
        pass

    def do_contact_check(self, lo_side: bool = False) -> tuple[bool, float]:
        """
        call enable_cc_mode(True) before calling this
        and enable_cc_mode(False) after you're done checking contacts
        attempts to turn on the output and trigger a measurement.
        tests if the output remains on after that. if so, the contact check passed
        True for contacted. always true if the sourcemeter hardware does not support this feature
        """
        if self.cc_mode.upper() == "NONE":
            ret = (True, 0.0)
        else:
            raise NotImplementedError("Contact check not available.")
        
        return ret

    def set_do(self, value: int):
        """sets digital output"""
        raise NotImplementedError("Digital output not available.")

    def decompose_idn(self) -> dict:
        """Convert idn string to dictionary of parts."""
        if not self.connected:
            raise ValueError("SMU not connected.")

        manufacturer, model, ctrl_firmware, ch_serial, ch_firmware = self.idn.split(",")

        return {
            "manufacturer": manufacturer,
            "model": model,
            "ctrl_firmware": ctrl_firmware,
            "ch_serial": ch_serial,
            "ch_firmware": ch_firmware,
        }


def run_test(address: str):
    """Run some test measurements.

    Parameters
    ----------
    address : str
        socket address formatted for pyserial: 'socket://{host}:{port}'.
    """
    # instrument config
    two_wire = True
    kws = {"line_frequency": 50}

    # general measurement config
    nplc = 1
    sense_range = "a"
    settling_delay = 0.005  # s
    source_voltage = True
    i_compliance = 0.1
    v_compliance = 2
    npoints = 11

    # dc voltage config
    v_dc = 1

    # voltage sweep config
    v_start = 0
    v_stop = 1

    # dc current config
    i_dc = 0.02

    # current sweep config
    i_start = -0.02
    i_stop = 0.03

    # dwell config
    t_dwell = 3  # s

    # voc config
    i_voc = 0  # s

    # run some measurements
    with AmSmu(address, two_wire=two_wire, **kws) as smu:
        print(f"Connected to: {smu.idn}")

        # make sure output starts disabled
        print("set output off")
        smu.outOn(False)

        # set measurement nplc
        print("set nplc")
        smu.setNPLC(nplc)

        # --- source voltage ---
        print("\nRunning source voltage, measure current mode...")

        # setup a DC svmi measurement and turn on output
        print("set up dc measurement")
        smu.setupDC(
            sourceVoltage=source_voltage,
            compliance=i_compliance,
            setPoint=v_dc,
            senseRange=sense_range,
        )

        # run DC measurement
        print("measuring...")
        dc_data = smu.measure(1)

        # turn off output
        print("set output off")
        smu.outOn(False)

        # print dc data after output has been disabled
        print(f"DC data: {dc_data}")

        # setup a svmi sweep and turn on output
        print("set up sweep")
        smu.setupSweep(
            sourceVoltage=source_voltage,
            compliance=i_compliance,
            nPoints=npoints,
            stepDelay=settling_delay,
            start=v_start,
            end=v_stop,
            senseRange=sense_range,
        )

        # run sweep measurement
        print("measuring...")
        sweep_data = smu.measure(npoints)

        # turn off output
        print("set output off")
        smu.outOn(False)

        # print sweep data after otuput has been disabled
        print(f"Sweep data: {sweep_data}")

        # # setup a DC svmi dwell measurement and turn on output
        # print("set up dwell measurement")
        # smu.setupDC(
        #     sourceVoltage=source_voltage,
        #     compliance=i_compliance,
        #     setPoint=v_dc,
        #     senseRange=sense_range,
        # )

        # # run dwell measurement
        # print("measuring...")
        # dwell_data = smu.measure_until(t_dwell)

        # # print dwell data after otuput has been disabled
        # print(f"Dwell data: {dwell_data}")
        # print(f"Dwell points: {len(dwell_data)}")
        # print(f"Dwell time: {dwell_data[-1][-2] - dwell_data[0][-2]} ms")

        # # trigger a dummy watchdog timeout and recover from it
        # print("triggering wdt...")
        # smu.reset(hard=True)

        # print("measuring...")
        # dc_data = smu.measure(1)

        # # turn off output
        # print("set output off")
        # smu.outOn(False)

        # # print dc data after output has been disabled
        # print(f"DC data: {dc_data}")

        # # --- source current ---
        # print("\nRunning source current, measure voltage mode...")

        # # setup a DC simv measurement and turn on output
        # print("set up dc measurement")
        # smu.setupDC(
        #     sourceVoltage=not source_voltage,
        #     compliance=v_compliance,
        #     setPoint=i_dc,
        #     senseRange=sense_range,
        # )

        # # run DC measurement
        # print("measuring...")
        # dc_data = smu.measure(1)

        # # turn off output
        # print("set output off")
        # smu.outOn(False)

        # # print dc data after output has been disabled
        # print(f"DC data: {dc_data}")

        # # setup a simv sweep and turn on output
        # print("set up sweep")
        # smu.setupSweep(
        #     sourceVoltage=not source_voltage,
        #     compliance=v_compliance,
        #     nPoints=npoints,
        #     stepDelay=settling_delay,
        #     start=i_start,
        #     end=i_stop,
        #     senseRange=sense_range,
        # )

        # # run sweep measurement
        # print("measuring...")
        # sweep_data = smu.measure(npoints)

        # # turn off output
        # print("set output off")
        # smu.outOn(False)

        # # print sweep data after otuput has been disabled
        # print(f"Sweep data: {sweep_data}")

        # # setup a DC simv dwell measurement and turn on output
        # print("set up dwell measurement")
        # smu.setupDC(
        #     sourceVoltage=not source_voltage,
        #     compliance=v_compliance,
        #     setPoint=i_dc,
        #     senseRange=sense_range,
        # )

        # # run dwell measurement
        # print("measuring...")
        # dwell_data = smu.measure_until(t_dwell)

        # # turn off output
        # print("set output off")
        # smu.outOn(False)

        # # print dwell data after otuput has been disabled
        # print(f"Dwell data: {dwell_data}")
        # print(f"Dwell points: {len(dwell_data)}")
        # print(f"Dwell time: {dwell_data[-1][-2] - dwell_data[0][-2]} ms")

        # # --- measure voc ---
        # print("\nRunning source current measure voltage at Voc...")

        # # setup a DC 0A measurement with output off
        # print("set up dc measurement")
        # smu.setupDC(
        #     sourceVoltage=not source_voltage,
        #     compliance=v_compliance,
        #     setPoint=i_voc,
        #     senseRange=sense_range,
        # )

        # # run DC measurement
        # print("measuring...")
        # dc_data = smu.measure(1)

        # # turn off output (though it should be off already)
        # print("set output off")
        # smu.outOn(False)

        # # print dc data after output has been disabled
        # print(f"DC data: {dc_data}")

        # # setup a DC 0A dwell measurement wiht output off
        # print("set up dwell measurement")
        # smu.setupDC(
        #     sourceVoltage=not source_voltage,
        #     compliance=v_compliance,
        #     setPoint=i_voc,
        #     senseRange=sense_range,
        # )

        # # run dwell measurement
        # print("measuring...")
        # dwell_data = smu.measure_until(t_dwell)

        # # turn off output (though it should be off already)
        # print("set output off")
        # smu.outOn(False)

        # # print dwell data after otuput has been disabled
        # print(f"Dwell data: {dwell_data}")
        # print(f"Dwell points: {len(dwell_data)}")
        # print(f"Dwell time: {dwell_data[-1][-2] - dwell_data[0][-2]} ms")


if __name__ == "__main__":
    import multiprocessing

    HOST = "SMU-A"
    PORTS = [50001, 50002, 50003, 50004]
    #PORTS = [50001]

    addresses = [f"socket://{HOST}:{port}" for port in PORTS]

    with multiprocessing.Pool(len(PORTS)) as pool:
        pool.map(run_test, addresses)
