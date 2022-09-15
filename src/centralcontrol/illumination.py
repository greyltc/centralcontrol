from centralcontrol.wavelabs import Wavelabs
from centralcontrol.virt import FakeLight

# from centralcontrol.newport import Newport

from threading import BrokenBarrierError
from threading import Barrier as tBarrier
from multiprocessing.synchronize import Barrier as mBarrier
from typing import Type, Callable

from centralcontrol.logstuff import get_logger


def factory(cfg: dict) -> Type["LightAPI"]:
    """light class factory
    give it a light source configuration dictionary and it will return the correct class to use
    """
    lg = get_logger(__name__)  # setup logging
    if "kind" in cfg:
        kind = cfg["kind"]
    else:
        lg.debug("Assuming wavelabs type light")
        kind = "wavelabs"

    base = FakeLight  # the default is to make a virtual light
    if ("virtual" in cfg) and (cfg["virtual"] is False):
        if "wavelabs" in kind:
            base = Wavelabs  # wavelabs selected

    if ("enabled" in cfg) and (cfg["enabled"] is False):
        base = DisabledLight  # disabled SMU selected

    name = LightAPI.__name__
    bases = (LightAPI, base)
    # tdict = ret.__dict__.copy()
    tdict = {}
    return type(name, bases, tdict)  # return the configured light class overlayed with our API


class LightAPI(object):
    """unified light programming interface"""

    barrier: mBarrier | tBarrier
    barrier_timeout = 10  # s. wait at most this long for thread sync on light state change
    _current_intensity: int = 0  # percent. 0 means off. otherwise can be on [10, 100]. what we believe the light's intensity is
    requested_intensity: int = 0  # percent. 0 means off. otherwise can be on [10, 100]. keeps track of what we want the light's intensity to be
    on_intensity = None  # the intensity value the hardware was initalized with. used in "on"
    get_spectrum: Callable[[], tuple[list[float], list[float]]]
    get_stemperatures: Callable[[], list[float]]
    last_temps: list[float]
    get_run_status: Callable[[], str]

    conn_status: int = -99  # connection status
    idn: str  # identification string

    init_args: tuple = ()
    init_kwargs: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        """just sets class variables"""
        self.lg = get_logger(".".join([__name__, type(self).__name__]))  # setup logging

        # store away the init args and kwargs
        self.init_args = args
        self.init_kwargs = kwargs

        if "intensity" in kwargs:
            self.on_intensity = int(kwargs["intensity"])  # use this initial intensity for the "on" value

        # thing that blocks to ensure sync
        self.barrier = tBarrier(1, action=self.apply_intensity, timeout=self.barrier_timeout)

        super(LightAPI, self).__init__(**kwargs)

    def __enter__(self) -> "LightAPI":
        """so that the smu can enter a context"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        """so that the smu can leave a context cleanly"""
        self.disconnect()
        return False

    def connect(self) -> None | int:
        """connects to and initalizes hardware"""
        if self.conn_status < 0:
            self.conn_status = super(LightAPI, self).connect()  # call the underlying connect method
            if self.conn_status < 0:
                self.lg.debug(f"Connection attempt failed with status {self.conn_status}")
            else:
                # good connection, let's sync the light's state
                if super(LightAPI, self).get_run_status() == "running":
                    self._current_intensity = super(LightAPI, self).get_intensity()
                else:
                    self._current_intensity = 0  # the light is off
        return self.conn_status

    def disconnect(self) -> None:
        """disconnect and clean up"""
        try:
            super(LightAPI, self).disconnect()  # call the underlying disconnect method
            self.conn_status = -80  # clean disconnection
        except Exception as e:
            self.conn_status = -89  # unclean disconnection
            self.lg.debug(f"Unclean disconnect: {repr(e)}")
        return None

    @property
    def lit(self) -> bool:
        return not (self.intensity == 0)

    @lit.setter
    def lit(self, value: bool):
        if value:
            if self.on_intensity is not None:
                setpoint = self.on_intensity
            else:
                setpoint = 100  # assume 100% if we don't know better
            self.intensity = setpoint
        else:
            self.intensity = 0
        return None

    @property
    def n_sync(self):
        """how many threads we need to wait for to synchronize"""
        return self.barrier.parties

    @n_sync.setter
    def n_sync(self, value: int) -> None:
        """
        update the number of threads we need to wait for to consider ourselves *NSYNC
        setting this while waiting for synchronization will raise a barrier broken error
        """

        self.barrier.abort()

        # thing that blocks to ensure sync
        self.barrier = tBarrier(value, action=self.apply_intensity, timeout=self.barrier_timeout)
        return None

    @property
    def intensity(self) -> int:
        """
        query the light's current intensity
        (might differ than requested intensity)
        """
        return self._current_intensity

    @intensity.setter
    def intensity(self, value: int) -> None:
        """set the intensity value you wish the light to be. ensures synchronization"""
        if isinstance(value, int) and ((value == 0) or ((value >= 10) and (value <= 100))):
            if value != self._current_intensity:
                self.lg.debug(f"Request to change light intensity to {value}. Waiting for synchronization...")
                self.requested_intensity = value
                try:
                    draw = self.barrier.wait()
                    if draw == 0:  # we're the lucky winner!
                        self.lg.debug(f"Light intensity synchronization complete!")
                    if value != self.requested_intensity:
                        self.lg.debug(f"Something's fishy. We wanted {value}, but got {self.requested_intensity}")
                except BrokenBarrierError as e:
                    # most likely a timeout
                    # could also be if the barrier was reset or aborted during the wait
                    # or if the call to change the light state errored
                    raise ValueError(f"The light synchronization barrier was broken! {e}")
            else:
                # requested state matches actual state
                self.lg.debug(f"Light intensity is already {value}")
        else:
            self.lg.debug(f"Invalid light intensity request: {value=}")

    def apply_intensity(self, forced_intensity: int | None = None) -> None:
        """
        set illumination intensity based on self.requested_intensity
        should not be called directly (instead use the barrier-enabled on parameter)
        unless you call it with force_intensity to an int to bypass the barrier-based thread sync interface
        """
        setpoint = forced_intensity
        if setpoint is None:
            setpoint = self.requested_intensity

        self.lg.debug(f"apply_intensity() doing {setpoint}")
        case = "B"
        if case == "A":  # non-blinky mode
            if setpoint == 0:
                set_ret = 0
                on_ret = "sn"  # dummy to always pass check
                off_ret = super(LightAPI, self).off()
            else:
                off_ret = 0
                set_ret = super(LightAPI, self).set_intensity(setpoint)
                on_ret = super(LightAPI, self).on()
        else:  # blinky mode
            off_ret = super(LightAPI, self).off()
            set_ret = 0
            on_ret = "sn"
            if setpoint != 0:
                set_ret = super(LightAPI, self).set_intensity(setpoint)
                on_ret = super(LightAPI, self).on()

        if (isinstance(on_ret, str) and on_ret.startswith("sn")) and (set_ret == 0) and (off_ret == 0):
            self._current_intensity = setpoint
        else:
            self.lg.debug(f"failure to set the light's intensity: {off_ret=} and {set_ret=} and {on_ret=}")


class DisabledLight(object):
    """this is the light class for when the user has disabled it"""

    def __init__(self, **kwargs):
        return None

    def __getattribute__(self, name):
        """handles any function call"""
        if hasattr(object, name):
            return object.__getattribute__(self, name)
        elif name == "idn":
            return "disabled"  # handle idn parameter check
        elif name == "connect":
            return lambda *args, **kwargs: 0  # connect() always returns zero
        else:
            return lambda *args, **kwargs: None  # all function calls return with none
