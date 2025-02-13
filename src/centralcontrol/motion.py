#!/usr/bin/env python3

import json
import math
from urllib.parse import parse_qs, urlparse

from centralcontrol.afms import AFMS
from centralcontrol.logstuff import get_logger
from centralcontrol.us import Us
from centralcontrol.stpdrv import Stpdrv
from centralcontrol.virt import FakeStpdrv
from centralcontrol.virt import FakeMC


class Motion(object):
    """generic class for handling substrate movement"""

    motion_engine = None
    home_procedure = "default"
    home_timeout = 130  # seconds
    motion_timeout_fraction = 1 / 2  # fraction of home_timeout for movement timeouts
    expected_lengths: dict[str, float]  # dict of keys=axis, vals =lengths in mm
    actual_lengths: dict[str, float]  # dict of keys=axis, vals =lengths in mm
    keepout_zones: dict[str, list[float]]  # dict of keys=axis, vals = list of mm
    empty_koz: list[float]  # a keepout zone that will never activate
    axes: list[str] # list of connected axis strings
    allowed_length_deviation = 5  # measured length can deviate from expected length by up to this, in mm
    location = "controller"

    motor_steps_per_rev = 200  # steps/rev
    micro_stepping = 256  # microsteps/step
    screw_pitch = 8  # mm/rev
    steps_per_mm = motor_steps_per_rev * micro_stepping / screw_pitch
    direction: int = 1  # can be 1 or -1 to invert what the "forward" direction means
    enabled = True

    address = "us://controller"

    def __init__(self, address: str | None = address, pcb_object=None, enabled=True, fake=False):
        """
        sets up communication to motion controller
        """
        # setup logging
        self.lg = get_logger(".".join([__name__, type(self).__name__]))  # setup logging
        self.expected_lengths= {}
        self.actual_lengths = {}
        self.keepout_zones = {}
        self.empty_koz = [-2, -2]
        self.axes = []
        self.address = address
        self.fake = fake

        self.enabled = enabled
        if address is None:
            self.enabled = False
        else:
            parsed = None
            qparsed = None
            try:
                parsed = urlparse(address)
                qparsed = parse_qs(parsed.query)
            except Exception:
                raise ValueError(f"Incorrect motion controller address format: {address}")
            self.location = parsed.netloc + parsed.path
            if "el" in qparsed:
                splitted = qparsed["el"][0].split(",")
                for i, els in enumerate(splitted):
                    self.expected_lengths[str(i + 1)] = float(els)
                    self.keepout_zones[str(i + 1)] = self.empty_koz  # ensure default koz works
            if "spm" in qparsed:
                self.steps_per_mm = int(qparsed["spm"][0])
            if "dir" in qparsed:
                self.direction = int(qparsed["dir"][0])
            if "kz" in qparsed:
                for i, zone in enumerate(json.loads(qparsed["kz"][0])):
                    if zone == []:
                        zone = self.empty_koz
                    self.keepout_zones[str(i + 1)] = zone
            if "hto" in qparsed:
                self.home_timeout = float(qparsed["hto"][0])
            if "homer" in qparsed:
                self.home_procedure = qparsed["homer"][0]
            if "lf" in qparsed:
                self.allowed_length_deviation = float(qparsed["lf"][0])

            if parsed.scheme == "afms":
                if pcb_object is not None:
                    if hasattr(pcb_object, "is_virtual"):
                        if pcb_object.is_virtual == True:
                            self.lg.warning("afms:// scheme does not support virtual PCBs")
                afms_setup = {}
                afms_setup["location"] = self.location
                afms_setup["spm"] = self.steps_per_mm
                afms_setup["homer"] = self.home_procedure
                self.motion_engine = AFMS(**afms_setup)
            elif parsed.scheme == "us":
                us_setup = {}
                us_setup["spm"] = self.steps_per_mm
                us_setup["dir"] = self.direction
                if (self.location != "mc") or (pcb_object is None):
                    raise ValueError(f"us://controller/ requires requires a pre-existing pcb_object")
                us_setup["pcb_object"] = pcb_object
                if hasattr(pcb_object, "is_virtual"):
                    if pcb_object.is_virtual == True:
                        pcb_object.prepare_virt_motion(spm=self.steps_per_mm, el=self.expected_lengths)
                self.motion_engine = Us(**us_setup)
            elif parsed.scheme == "stpdrv":
                if self.fake:
                    us_setup = {}
                    us_setup["spm"] = self.steps_per_mm
                    us_setup["dir"] = self.direction
                    pcb_object = FakeMC()
                    us_setup["pcb_object"] = pcb_object
                    pcb_object.prepare_virt_motion(spm=self.steps_per_mm, el=self.expected_lengths)
                    self.motion_engine = Us(**us_setup)
                else:
                    stpdrv_setup = {}
                    stpdrv_setup["address"] = self.location
                    stpdrv_setup["steps_per_mm"] = self.steps_per_mm
                    stpdrv_setup["motion_timeout"] = self.home_timeout
                    self.motion_engine = Stpdrv(**stpdrv_setup)
            else:
                raise ValueError(f"Unexpected motion controller protocol {parsed.scheme} in {address}")

        self.lg.debug(f"{__name__} initialized.")

    def connect(self):
        """makes connection to motion controller and does a light check that the given axes config is correct"""
        self.lg.debug(f"motion.connect() called")
        if self.enabled:
            assert self.motion_engine is not None, f"{self.motion_engine is not None=}"
            result = self.motion_engine.connect()
            if result == 0:
                self.actual_lengths = self.motion_engine.len_axes_mm
                self.axes = self.motion_engine.axes

                naxes = len(self.axes)
                nlengths = len(self.actual_lengths)
                nexpect = len(self.expected_lengths)
                nzones = len(self.keepout_zones)

                if naxes != nlengths:
                    raise ValueError(f"Error: axis count mismatch. Measured {nlengths} lengths, but the hardware reports {naxes} axes")
                if naxes != nexpect:
                    raise ValueError(f"Error: axis count mismatch. Found {nexpect} expected lengths, but the hardware reports {naxes} axes")
                if naxes != nzones:
                    raise ValueError(f"Error: axis count mismatch. Found {nexpect} keepout zone lists, but the hardware reports {naxes} axes")

                for ax in self.axes:
                    if self.actual_lengths[ax] <= 0:
                        self.lg.warning(f"Warning: axis {ax} is not ready for motion. Please press the 'Recalibrate' button (ignore this message if you just did that).")

            self.lg.debug(f"motion connected")
        else:
            result = 0
        return result

    #  def move(self, mm):
    #    """
    #    moves mm mm direction, blocking, returns 0 on successful movement
    #    """
    #    return self.motion_engine.move(mm)

    def goto(self, pos, timeout=300, debug_prints=False):
        """goes to an absolute mm position, blocking"""
        self.lg.debug(f"goto({pos=}) called")
        if self.enabled:
            assert self.motion_engine is not None, f"{(self.motion_engine is not None)=}"
            if timeout == None:
                timeout = self.home_timeout * self.motion_timeout_fraction
            if not hasattr(pos, "__len__"):
                pos = [pos]
            naxes = len(self.axes)
            npos = len(pos)
            if naxes != npos:
                raise ValueError(f"Error: axis count mismatch. Found {npos} commanded positions, but the hardware reports {naxes} axes")
            gti = {}
            for i, ax in enumerate(self.axes):
                el = self.expected_lengths[ax]
                al = self.actual_lengths[ax]
                ko_lower = self.keepout_zones[ax][0]
                ko_upper = self.keepout_zones[ax][1]
                lower_lim = 0 + self.motion_engine.end_buffers
                upper_lim = al - self.motion_engine.end_buffers
                goal = pos[i]
                if not math.isnan(goal):
                    if el < float("inf"):  # length check is enabled
                        delta = el - al
                        if abs(delta) > self.allowed_length_deviation:
                            raise ValueError(f"Error: Unexpected axis {ax} length. Found {al} [mm] but expected {el} [mm]")
                    if (goal >= ko_lower) and (goal <= ko_upper):
                        raise ValueError(f"Error: Axis {ax} requested position, {goal} [mm], falls within keepout zone: [{ko_lower}, {ko_upper}] [mm]")
                    if goal < lower_lim:
                        raise ValueError(f"Error: Attempt to move axis {ax} outside of limits. Attempt: {goal} [mm], but Minimum: {lower_lim} [mm]")
                    if goal > upper_lim:
                        raise ValueError(f"Error: Attempt to move axis {ax} outside of limits. Attempt: {goal} [mm], but Maximum: {upper_lim} [mm]")
                    gti[ax] = goal
            self.motion_engine.goto(gti, timeout=timeout, debug_prints=debug_prints)
        self.lg.debug(f"goto() complete")

    def home(self, timeout=300.0):
        """homes to a limit switch, blocking, reuturns 0 on success"""
        self.lg.debug(f"home() called")
        if self.enabled:
            assert self.motion_engine is not None, f"{self.motion_engine is not None=}"
            if timeout is None:
                timeout = self.home_timeout
            home_setup = {}
            home_setup["procedure"] = self.home_procedure
            home_setup["timeout"] = timeout
            home_setup["expected_lengths"] = self.expected_lengths
            home_setup["allowed_deviation"] = self.allowed_length_deviation
            home_result = self.motion_engine.home(**home_setup)
            self.actual_lengths = self.motion_engine.len_axes_mm
        self.lg.debug(f"home() complete")

    def estop(self):
        """emergency stop of the driver"""
        self.lg.debug("motion estop() called")
        if self.enabled:
            assert self.motion_engine is not None, f"{self.motion_engine is not None=}"
            ret = self.motion_engine.estop()
        else:
            ret = 0
        self.lg.debug("motion estop() complete")
        return ret

    def get_position(self):
        """returns the current stage location in mm"""
        self.lg.debug("motion get_position() called")
        if self.enabled:
            assert self.motion_engine is not None, f"{self.motion_engine is not None=}"
            pos = self.motion_engine.get_position()
        else:
            pos = [0] * len(self.axes)
        self.lg.debug(f"motion get_position() complete with {pos=}")
        return pos


# testing
def main():
    import time

    fake_hardware = False
    if fake_hardware == True:
        from .virt import FakeMC as pcbclass

        pcbobj_init_args = {}
    else:
        from .mc import MC as pcbclass

        pcbobj_init_args = {}
        office_ip = "10.46.0.239"
        pcbobj_init_args["address"] = office_ip
    otter_config_uri = "us://controller?el=875,375&kz=[[],[0,62]]&spm=6400&hto=130&homer=2b!1h!1g650!2h"
    oxford_config_uri = "us://controller?el=375"
    office_config_uri = "us://controller?el=125"
    stage_config_uri = office_config_uri

    print(f'Connecting to a {"fake" if fake_hardware == True else "real"} stage with URI-->{stage_config_uri}')
    with pcbclass(**pcbobj_init_args) as p:
        mo = Motion(address=stage_config_uri, pcb_object=p)
        mo.connect()
        print(f"Connected.")
        print(f"Measured lengths: {mo.actual_lengths}")
        print(f"Axes: {mo.axes}")

        print("Initiating homing prodecure...")
        mo.home()
        print("Homing complete.")
        print(f"Measured lengths: {mo.actual_lengths}")

        print(f"Current Position: {mo.get_position()}")

        mid = [x / 2 for ax, x in mo.actual_lengths.items()]
        print(f"Going to midway: {mid}")
        mo.goto(mid)
        print("Done.")
        here = mo.get_position()
        print(f"Current Position: {here}")

        # choose how long the dance should last
        # goto_dance_duration = float("inf")
        goto_dance_duration = 60
        dance_axis = "0"  # which axis to dance
        print(f"Now doing goto dance for {goto_dance_duration} seconds...")

        dance_width_mm = 5
        ndancepoints = 10

        dancemin = 4 + dance_width_mm / 2
        dancemax = mo.actual_lengths[dance_axis] - dancemin
        dancespace = [dancemin + float(x) / (ndancepoints - 1) * (dancemax - dancemin) for x in range(ndancepoints)]
        dancepoints = []
        for p in dancespace:
            dancepoints.append(p - dance_width_mm / 2)
            dancepoints.append(p + dance_width_mm / 2)
            dancepoints.append(p - dance_width_mm / 2)

        dancepoints_rev = dancepoints.copy()
        dancepoints_rev.reverse()
        del dancepoints_rev[0]
        del dancepoints_rev[-1]
        full_dancelist = dancepoints + dancepoints_rev

        t0 = time.time()
        target = here
        while (time.time() - t0) < goto_dance_duration:
            goal = full_dancelist.pop(0)
            target[int(dance_axis) - 1] = goal
            print(f"New target = {target}")
            mo.goto(target, debug_prints=True)
            full_dancelist.append(goal)  # allow for wrapping
        print(f"Dance complete!")

        print(f"Doing emergency stop.")
        mo.estop()

    print()


if __name__ == "__main__":
    main()
