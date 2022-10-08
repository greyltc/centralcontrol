from typing import Callable, Type, Optional
from threading import Event as tEvent
from multiprocessing.synchronize import Event as mEvent
from centralcontrol.virt import FakeSMU as vsmu
from centralcontrol.k2400 import k2400
from centralcontrol.logstuff import get_logger


def factory(cfg: dict) -> Type["SourcemeterAPI"]:
    """sourcemeter class factory
    give it a smu configuration dictionary and it will return the correct smu class to use
    """
    lg = get_logger(__name__)  # setup logging
    if "kind" in cfg:
        kind = cfg["kind"]
    else:
        lg.debug("Assuming k24xx type smu")
        kind = "k24xx"

    base = vsmu  # the default is to make a virtual smu type
    if ("virtual" in cfg) and (cfg["virtual"] is False):
        if kind == "k24xx":
            base = k2400  # hardware k2400 selected

    if ("enabled" in cfg) and (cfg["enabled"] is False):
        base = DisabledSMU  # disabled SMU selected

    name = SourcemeterAPI.__name__
    bases = (SourcemeterAPI, base)
    # tdict = ret.__dict__.copy()
    tdict = {}
    return type(name, bases, tdict)  # return the configured smu class overlayed with our API


class SourcemeterAPI(object):
    """unified sourcemeter programming interface"""

    address: str
    device_grouping: list[list[str]]
    conn_status: int = -99  # connection status
    idn: str | None = None  # identification string
    id: int = 0  # id from db
    init_args: tuple = ()
    init_kwargs: dict = {}
    killer: mEvent | tEvent
    setNPLC: Callable[[float], None]
    outOn: Callable[[bool], None]
    measure: Callable[..., list[tuple[float, float, float, int]] | list[tuple[float, float, float, float, int]]]
    setupSweep: Callable[..., None]
    # setupDC: Callable[..., None]
    measure_until: Callable[..., list[tuple[float, float, float, int]] | list[tuple[float, float, float, float, int]]]
    enable_cc_mode: Callable[[bool], None]
    do_contact_check: Callable[[bool], tuple[bool, float]]
    threshold_ohm: float
    voltage_limit: float = 3
    current_limit: float = 0.150

    # measure: Callable[[int | None], list[tuple[float, float, float, int]] | list[tuple[float, float, float, float, int]]]
    # setupSweep: Callable[[bool | None, float | None, int | None, float | None, float | None, str | None], None]
    # setupDC: Callable[[bool | None, float | None, float | None, str | None, str | None | bool], None]
    # measure_until: Callable[[Optional[float], Optional[float], Optional[Callable]], list[tuple[float, float, float, int]] | list[tuple[float, float, float, float, int]]]

    def __init__(self, *args, **kwargs) -> None:
        """just sets class variables"""
        self.lg = get_logger(".".join([__name__, type(self).__name__]))

        # store away the init args and kwargs
        self.init_args = args
        self.init_kwargs = kwargs
        if "voltage_limit" in kwargs:
            self.voltage_limit = kwargs["voltage_limit"]
        if "current_limit" in kwargs:
            self.current_limit = kwargs["current_limit"]

        super(SourcemeterAPI, self).__init__(**kwargs)
        return None

    def __enter__(self) -> "SourcemeterAPI":
        """so that the smu can enter a context"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        """so that the smu can leave a context cleanly"""
        self.disconnect()
        return False

    def connect(self) -> None:
        """connects to and initalizes hardware"""
        if self.conn_status < 0:
            self.conn_status = super(SourcemeterAPI, self).connect()  # call the underlying connect method
            if self.conn_status < 0:
                self.lg.debug(f"Connection attempt failed with status {self.conn_status}")
        return None

    def disconnect(self) -> None:
        """disconnect and clean up"""
        try:
            super(SourcemeterAPI, self).disconnect()  # call the underlying disconnect method
            self.conn_status = -80  # clean disconnection
        except Exception as e:
            self.conn_status = -89  # unclean disconnection
            self.lg.debug(f"Unclean disconnect: {e}")
        return None

    @staticmethod
    def which_smu(device_grouping: list[list[list]], devaddr: list) -> None | int:
        """given a device address, and device_grouping,
        returns the index of the SMU connected to it"""
        ret = None
        if device_grouping is not None:
            for group in device_grouping:
                if devaddr in group:
                    ret = group.index(devaddr)
                    break
        return ret

    def setupDC(self, sourceVoltage: bool = True, compliance: float = 0.04, setPoint: float = 0.0, senseRange: str = "f", ohms: str | bool = False):
        if sourceVoltage:
            assert abs(setPoint) < self.voltage_limit, "Voltage setpoint over limit"
            compliance = min(compliance, self.current_limit)
        else:
            assert abs(setPoint) < self.current_limit, "Current setpoint over limit"
            compliance = min(compliance, self.voltage_limit)
        return super(SourcemeterAPI, self).setupDC(sourceVoltage=sourceVoltage, compliance=compliance, setPoint=setPoint, senseRange=senseRange, ohms=ohms)

    # TODO: add more API!


class DisabledSMU(object):
    """this is the smu class for when the user has disabled it"""

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


if __name__ == "__main__":
    cfg = {}
    cfg["enabled"] = True
    cfg["virtual"] = True
    smuc = factory(cfg)
    sm = smuc(**cfg)
    sm.ding = "dong"
    # sm.connect(ass=3)
    print(sm.idn)
    print(sm.ding)

    cfg = {}
    cfg["enabled"] = False
    cfg["virtual"] = True
    smuc = factory(cfg)
    sm = smuc(**cfg)
    sm.ding = "dong"
    sm.connect(ass=3)
    print(sm.idn)
    print(sm.ding)
    # the disabled class doesn't quite act right when getting attributes that aren't "idn"
