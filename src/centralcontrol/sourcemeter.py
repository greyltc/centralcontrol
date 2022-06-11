# from __future__ import annotations

# from typing import Type, TypeVar, ClassVar, Union
import logging

import typing
from centralcontrol.virt import smu as vsmu
from centralcontrol.k2400 import k2400


def factory(cfg: typing.Dict) -> typing.Type["SourcemeterAPI"]:
    """sourcemeter class factory
    give it a smu configuration dictionary and it will return the correct smu class to use
    also handles disabled smus by reutrning None
    """
    logname = __name__
    if isinstance(__package__, str) and __package__ in __name__:
        # log at the package level if the imports are all correct
        logname = __package__
    lg = logging.getLogger(logname)
    lg.setLevel(logging.DEBUG)

    if "type" in cfg:
        smu_type = cfg["type"]
    else:
        lg.info("Assuming k2400 type smu")
        smu_type = "k2400"

    ret = vsmu
    if ("virtual" in cfg) and (cfg["virtual"] is False):
        if smu_type == "k2400":
            ret = k2400

    if ("enabled" in cfg) and (cfg["enabled"] is False):
        lg.info("SMU disabled")
        ret = DisabledSMU

    if ret == vsmu:
        lg.info("Using a virtual SMU")

    bases = (SourcemeterAPI, ret)
    return type(SourcemeterAPI.__name__, bases, {})  # return the configured smu class overlayed with our API


class SourcemeterAPI(object):
    """unified sourcemeter programming interface"""

    idn: str
    quiet: bool
    device_grouping: typing.List[typing.List[str]]

    def __init__(self, **kwargs) -> None:
        """just sets class variables"""
        super(SourcemeterAPI, self).__init__(**kwargs)

    def connect(self) -> None:
        """connects to and initalizes hardware"""
        return super(SourcemeterAPI, self).connect()

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
        else:
            return lambda *args, **kwargs: None  # all function calls return with none


if __name__ == "__main__":
    cfg = {}
    cfg["enabled"] = False
    cfg["virtual"] = True
    smuc = factory(cfg)
    sm = smuc(**cfg)
    sm.c
    sm = smuc(**cfg)
    sm.connect(ass=3)
    print(sm.idn)
