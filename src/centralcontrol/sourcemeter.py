import typing
from centralcontrol.virt import smu as vsmu
from centralcontrol.k2400 import k2400

try:
    from centralcontrol.logstuff import get_logger as getLogger
except:
    from logging import getLogger


def factory(cfg: typing.Dict) -> typing.Type["SourcemeterAPI"]:
    """sourcemeter class factory
    give it a smu configuration dictionary and it will return the correct smu class to use
    """
    lg = getLogger(__name__)  # setup logging
    if "kind" in cfg:
        kind = cfg["kind"]
    else:
        lg.info("Assuming k24xx type smu")
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

    device_grouping: typing.List[typing.List[str]]
    conn_status: int = -99  # connection status
    idn: str

    def __init__(self, **kwargs) -> None:
        """just sets class variables"""
        self.lg = getLogger(".".join([__name__, type(self).__name__]))  # setup logging

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

    def which_smu(self, devaddr: str) -> int:
        """given a device address, returns the index of the SMU connected to it
        you must register device_grouping before this will work"""
        ret = None
        if self.device_grouping is not None:
            for group in self.device_grouping:
                if devaddr in group:
                    ret = group.index(devaddr)
                    break
        return ret

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
