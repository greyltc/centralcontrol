"""Ark Metrica Stepper Motor Controller control library."""

from typing import Dict
import warnings

import serial


class Stpdrv:
    """Ark Metrica Stepper Motor Controller."""

    WRITE_TERMINATION = "\r"
    READ_TERMINATION = "\r"

    # maximum number of times to retry a query
    MAX_RETRIES = 3

    axes = ["1"]

    # centralcontral uses the limits as position 0 and len and calculates allowed
    # offsets from them in software. The Stepper Motor Controller implements the
    # offsets from the limits in firmware so set this to 0.
    end_buffers = 0

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context related to this object.

        Make sure everything gets cleaned up properly.
        """
        self.disconnect()

    def __init__(
        self,
        address: str,
        steps_per_mm: float,
        motion_timeout: float = 120.0,
        speed: float = 16,
        acceleration: float = 16,
        limit_offset: float = 20,
        resource_kwargs: Dict = {},
    ):
        """Construct object.

        Parameters
        ----------
        address : str
            Serial port address, e.g. COM1, /dev/ttyUSB0 etc.
        steps_per_mm : float
            Number of motor steps per mm of linear actuator travel.
        limit_offset : float
            How far to move away from a limit switch when homing, in mm.
        motion_timeout : int
            Time in s to wait before aborting a motion command.
        speed : float
            Linear actuator speed in mm/s.
        accel : float
            Linear actuator acceleration in mm/s^2.
        resource_kwargs : dict
            Additional arguments to be passed to the instrument comms constructor, e.g.
            baudrate etc.
        """
        self.steps_per_mm = steps_per_mm
        self._limit_offset = limit_offset
        self._motion_timeout = motion_timeout
        self._speed = speed
        self._acceleration = acceleration

        self._address = address
        self._resource_kwargs = resource_kwargs
        self.instr = None

    # TODO: implement limit offset
    # @property
    # def limit_offset(self) -> float:
    #     """Get limit offset in mm.

    #     Returns
    #     -------
    #     limit_offset : float
    #         How far to move away from a limit switch when homing, in mm.
    #     """
    #     if self.instr is not None:
    #         self._limit_offset = self.steps_to_mm(self._query())

    #     return self._limit_offset

    # @limit_offset.setter
    # def limit_offset(self, limit_offset) -> None:
    #     """Set the limit offset in mm.

    #     Parameters
    #     ----------
    #     limit_offset : float
    #         How far to move away from a limit switch when homing, in mm.
    #     """
    #     self._limit_offset = limit_offset
    #     if self.instr is not None:
    #         cmd = f"{}{self.mm_to_steps(self._limit_offset)}"
    #         self._query(cmd)

    @property
    def address(self) -> str:
        """Get the resource name.

        Returns
        -------
        address : str
            VISA resource name.
        """
        return self._address

    @address.setter
    def address(self, address: str):
        """Set the resource name if instrument isn't connected.

        Parameters
        ----------
        address : str
            VISA resource name.
        """
        if self.instr is not None:
            self._address = address
        else:
            warnings.warn("Cannot change the resource name while the instrument is connected.")

    @property
    def resource_kwargs(self) -> Dict:
        """Get the resource kwargs.

        This is a read-only property. The keyword arguments cannot be changed safely
        after the object is initialised.

        Returns
        -------
        resource_kwargs : str
            Additional keyword arguments passed to the pyvisa resource constructor.
        """
        return self._resource_kwargs

    @property
    def motion_timeout(self) -> float:
        """Get the motion timeout.

        Returns
        -------
        motion_timeout : float
            Time in ms to wait before aborting a motion command.
        """
        if self.instr is not None:
            self._motion_timeout = float(self._query("t")) / 1000

        return self._motion_timeout

    @motion_timeout.setter
    def motion_timeout(self, motion_timeout: float):
        """Set the motion timeout.

        Parameters
        ----------
        motion_timeout : int
            Time in s to wait before aborting a motion command.
        """
        self._motion_timeout = motion_timeout
        if self.instr is not None:
            # set comms timeout to be slightly longer than homing timeout
            self.instr.timeout = self._motion_timeout * 2.2

            # set firmware timeout
            cmd = f"t{round(self._motion_timeout * 1000)}"
            self._query(cmd)

    @property
    def speed(self) -> float:
        """Get the speed.

        Returns
        -------
        speed : float
            Linear actuator speed in mm/s.
        """
        if self.instr is not None:
            self._speed = self.steps_to_mm(float(self._query("s")))

        return self._speed

    @speed.setter
    def speed(self, speed: float):
        """Set the linear actuator speed.

        Parameters
        ----------
        speed : float
            Linear actuator speed in mm/s.
        """
        self._speed = speed
        if self.instr is not None:
            cmd = f"s{self.mm_to_steps(self._speed)}"
            self._query(cmd)

    @property
    def acceleration(self) -> float:
        """Get the acceleration.

        Returns
        -------
        acceleration : float
            Linear actuator acceleration in mm/s^2.
        """
        if self.instr is not None:
            self._acceleration = self.steps_to_mm(float(self._query("a")))

        return self._acceleration

    @acceleration.setter
    def acceleration(self, acceleration: float):
        """Set the linear actuator acceleration.

        Parameters
        ----------
        acceleration : float
            Linear actuator acceleration in mm/s^2.
        """
        self._acceleration = acceleration
        if self.instr is not None:
            cmd = f"a{self.mm_to_steps(self._acceleration)}"
            self._query(cmd)

    def connect(self) -> None:
        """Connect to the instrument.

        Parameters
        ----------

        resource_manager : pyvisa.ResourceManager, optional
            Resource manager used to create new connection. If `None`, create a new
            resource manager using system set VISA backend.
        resource_kwargs : dict
            Keyword arguments passed to PyVISA resource to be used to change
            instrument attributes after construction.
        """
        # self.instr = serial.Serial(self.address, **self.resource_kwargs)
        self.instr = serial.Serial(self.address, baudrate=19200)

        # set initialisation comms timeout
        self.instr.timeout = 1
        self.instr.write_timeout = 1

        # synchronise communications by sending an invalid command until the correct
        # response is received
        for i in range(10):
            try:
                self.instr.write(f"Test{self.WRITE_TERMINATION}".encode("ascii"))
                resp = self.instr.read_until(self.READ_TERMINATION.encode("ascii"))
                if resp.startswith(b"ERROR: Invalid command: "):
                    break
            except Exception as err:
                if i == 9:
                    raise RuntimeError("Couldn't initialise stepper controller") from err

        # initialise firmware parameters
        self.speed = self._speed
        self.acceleration = self._acceleration
        self.motion_timeout = self._motion_timeout
        # TODO: self.limit_offset = self._limit_offset

    def disconnect(self) -> None:
        """Disconnect instrument."""
        self.estop()
        self.instr.close()
        self.instr = None

    def _query(self, cmd: str) -> str:
        """Query the instrument with error check.

        Parameters
        ----------
        cmd : str
            Command string to query.

        Returns
        -------
        resp : str
            Response string.
        """
        if self.instr is None:
            raise ValueError("Cannot query a command because controller hasn't been connected.")

        # send queries with retries to catch problems with serial transmitting
        # nonsense bytes
        for _ in range(self.MAX_RETRIES):
            cmd_fmt = f"{cmd}{self.WRITE_TERMINATION}".encode("ascii")
            self.instr.write(cmd_fmt)
            resp = self.instr.read_until(self.READ_TERMINATION.encode("ascii"))
            if not resp.startswith(b"ERROR"):
                break

        if resp.startswith(b"ERROR"):
            raise ValueError(f"Sent: {cmd_fmt} but received: {resp}")

        return resp.decode("ascii").strip(self.READ_TERMINATION)

    def mm_to_steps(self, distance: float) -> int:
        """Convert a distance in mm to a number of steps.

        Parameters
        ----------
        distance : float
            Distance in mm to convert.

        Returns
        -------
        steps : int
            Equivalent number of steps rounded to the nearest whole step.
        """
        return round(distance * self.steps_per_mm)

    def steps_to_mm(self, steps: int | float) -> float:
        """Convert a number of steps into an equivalent distance in mm.

        Parameters
        ----------
        steps : int or float
            Number of steps to convert.

        Returns
        -------
        distance : float
            Equivalent distance in mm.
        """
        return steps / self.steps_per_mm

    @property
    def idn(self) -> str:
        """Get identity string of the controller.

        Returns
        -------
        idn : str
            Identity string of the controller board.
        """
        return self._query("i")

    @property
    def len_axes_mm(self) -> Dict[str, float]:
        """Get the axis length in mm.

        Returns a dictionary for compatibility with centralcontrol.

        Returns
        -------
        len_axes_mm : Dict

        """
        length = int(self._query("l"))
        return {self.axes[0]: length if length <= 0 else self.steps_to_mm(length)}

    def goto(self, target: Dict[str, float], timeout: float = 120, debug_prints: bool = True) -> None:
        """Go to a target position.

        Uses dictionaries for target, include timeout arg, and include debug_prints arg
        for compatibility with centralcontrol.

        Parameters
        ----------
        target : dict
            Dictionary with axis numbers as keys and target positions in mm as values.
        timeout : float
            Timeout in s.
        debug_prints : bool
            Ignored. Present for compatibility with centralcontrol.
        """
        self.motion_timeout = timeout
        self._query(f"g{self.mm_to_steps(target[self.axes[0]])}")

    def home(
        self,
        procedure: str = "default",
        timeout: float = 120,
        expected_lengths: Dict[str, float] | None = None,
        allowed_deviation: float | None = None,
    ) -> None:
        """Home the stage.

        Arguments are for compatibility with centralcontrol.

        Parameters
        ----------
        procedure : str
            Ignored.
        timeout : float
            Homing timeout in s.
        expected_lengths : dict
            Dictionary with axis numbers as keys and expected length in mm as values.
        allowed_deviation : float
            Allowed deviation of expected lengths with actual lengths fouund during
            homing.
        """
        # homing takes two motions so divide timeout by 2
        # moving away from limits won't be counted
        self.motion_timeout = timeout / 2

        # request homing
        self._query("h")

        # check if length is as expected if details are provided
        if (allowed_deviation is not None) and (expected_lengths is not None):
            _len_axes_mm = self.len_axes_mm[self.axes[0]]
            if abs(_len_axes_mm - expected_lengths[self.axes[0]]) > allowed_deviation:
                raise ValueError(f"Unexpected axis length. Found {_len_axes_mm} [mm] but expected " + f"{expected_lengths[self.axes[0]]} [mm]")

    def estop(self) -> None:
        """Request emergency stop."""
        self._query("b")

    def get_position(self) -> Dict[str, float]:
        """Get current position in mm.

        Returns a dictionary for compatibility with central control.

        Returns
        -------
        pos : dict
            Dictionary with axes as keys and current position in mm as values.
        """
        return {self.axes[0]: self.steps_to_mm(int(self._query("r")))}
