#!/usr/bin/env python3

from __future__ import division

import time
from collections import deque

import sys
import logging

# for logging directly to systemd journal if we can
try:
    import systemd.journal
except ImportError:
    pass

# this boilerplate is required to allow this module to be run directly as a script
if __name__ == "__main__" and __package__ in [None, ""]:
    __package__ = "centralcontrol"
    from pathlib import Path
    import sys

    # get the dir that holds __package__ on the front of the search path
    sys.path.insert(0, str(Path(__file__).parent.parent))


class Us(object):
    """interface to uStepperS via i2c via ethernet connected pcb"""

    # calculate a default steps_per_mm value
    motor_steps_per_rev = 200  # steps/rev
    micro_stepping = 256  # microsteps/step
    screw_pitch = 8  # mm/rev
    steps_per_mm = motor_steps_per_rev * micro_stepping / screw_pitch
    home_procedure = "default"
    pcb = None
    len_axes_mm = {}  # dict of axis names = keys, lengths = values
    axes = [1]
    poll_delay = 0.25  # number of seconds to wait between polling events when trying to figure out if home, jog or goto are finsihed

    end_buffers = 4  # disallow movement to closer than this many mm from an end (prevents home issues)

    # stepper driver IC register locations
    TMC5130_GCONF = 0x00
    TMC5130_GSTAT = 0x01
    TMC5130_IFCNT = 0x02
    TMC5130_SLAVECONF = 0x03
    TMC5130_INP_OUT = 0x04
    # TMC5130_IOIN=0x04
    TMC5130_X_COMPARE = 0x05

    TMC5130_IHOLD_IRUN = 0x10
    TMC5130_TPOWERDOWN = 0x11
    TMC5130_TSTEP = 0x12
    TMC5130_TPWMTHRS = 0x13
    TMC5130_TCOOLTHRS = 0x14
    TMC5130_THIGH = 0x15

    TMC5130_RAMPMODE = 0x20
    TMC5130_XACTUAL = 0x21
    TMC5130_VACTUAL = 0x22
    TMC5130_VSTART = 0x23
    TMC5130_A1 = 0x24
    TMC5130_V1 = 0x25
    TMC5130_AMAX = 0x26
    TMC5130_VMAX = 0x27
    TMC5130_DMAX = 0x28
    TMC5130_D1 = 0x2A
    TMC5130_VSTOP = 0x2B
    TMC5130_TZEROWAIT = 0x2C
    TMC5130_XTARGET = 0x2D

    TMC5130_VDCMIN = 0x33
    TMC5130_SWMODE = 0x34
    TMC5130_RAMPSTAT = 0x35
    TMC5130_XLATCH = 0x36

    TMC5130_ENCMODE = 0x38
    TMC5130_XENC = 0x39
    TMC5130_ENC_CONST = 0x3A
    TMC5130_ENC_STATUS = 0x3B
    TMC5130_ENC_LATCH = 0x3C

    TMC5130_MSLUT0 = 0x60
    TMC5130_MSLUT1 = 0x61
    TMC5130_MSLUT2 = 0x62
    TMC5130_MSLUT3 = 0x63
    TMC5130_MSLUT4 = 0x64
    TMC5130_MSLUT5 = 0x65
    TMC5130_MSLUT6 = 0x66
    TMC5130_MSLUT7 = 0x67
    TMC5130_MSLUTSEL = 0x68
    TMC5130_MSLUTSTART = 0x69
    TMC5130_MSCNT = 0x6A
    TMC5130_MSCURACT = 0x6B

    TMC5130_CHOPCONF = 0x6C
    TMC5130_COOLCONF = 0x6D
    TMC5130_DCCTRL = 0x6E
    TMC5130_DRVSTATUS = 0x6F
    TMC5130_PWMCONF = 0x70
    TMC5130_PWMSTATUS = 0x71
    TMC5130_ENCM_CTRL = 0x72
    TMC5130_LOST_STEPS = 0x73

    def __init__(self, pcb_object, spm=steps_per_mm, homer=home_procedure):
        """sets up the microstepper object, needs handle to active PCB class object"""
        self.pcb = pcb_object
        self.steps_per_mm = spm
        self.home_procedure = homer
        self.stage_firmwares = {}

        # setup logging
        self.lg = logging.getLogger(__name__)
        self.lg.setLevel(logging.DEBUG)

        if not self.lg.hasHandlers():
            # set up logging to systemd's journal if it's there
            if "systemd" in sys.modules:
                sysdl = systemd.journal.JournalHandler(SYSLOG_IDENTIFIER=self.lg.name)
                sysLogFormat = logging.Formatter(("%(levelname)s|%(message)s"))
                sysdl.setFormatter(sysLogFormat)
                self.lg.addHandler(sysdl)
            else:
                # for logging to stdout & stderr
                ch = logging.StreamHandler()
                logFormat = logging.Formatter(("%(asctime)s|%(name)s|%(levelname)s|%(message)s"))
                ch.setFormatter(logFormat)
                self.lg.addHandler(ch)

        self.lg.debug(f"{__name__} initialized.")

    # wrapper for handling firmware comms that should return ints
    def _pwrapint(self, cmd):
        answer = self.pcb.query(cmd)
        intans = 0
        try:
            intans = int(answer)
        except ValueError:
            raise ValueError(f"Expecting integer response to {cmd}, but got {answer}")
        return intans

    def _update_len_axes_mm(self):
        len_axes_mm = {}
        for ax in self.axes:
            len_axes_mm[ax] = self._pwrapint(f"l{ax}") / self.steps_per_mm
        self.len_axes_mm = len_axes_mm

    def connect(self):
        """opens connection to the motor controller and sets self.actual_lengths"""
        self.pcb.probe_axes()
        self.axes = self.pcb.detected_axes
        self._update_len_axes_mm()
        for ax in self.axes:
            fw_cmd = f"w{ax}"
            self.stage_firmwares[ax] = self.pcb.query(fw_cmd)
        return 0

    def home(self, procedure="default", timeout=300, expected_lengths=None, allowed_deviation=None):
        t0 = time.time()
        self.pcb.probe_axes()
        self.axes = self.pcb.detected_axes
        self._update_len_axes_mm()

        # wait 2 seconds for them to complete their resets
        for ax in self.axes:
            self.reset(ax)

        if procedure == "default":
            for ax in self.axes:
                home_cmd = f"h{ax}"
                answer = self.pcb.query(home_cmd)
                if answer != "":
                    raise ValueError(f"Request to home axis {ax} via '{home_cmd}' failed with {answer}")
                else:
                    self._wait_for_home_or_jog(ax, timeout=timeout - (time.time() - t0))
                    if self.len_axes_mm[ax] == 0:
                        raise ValueError(f"Calibration of axis {ax} resulted in measured length of zero.")
                    else:
                        self.lg.info(f"Calibration complete. Axes lengths are {self.len_axes_mm}")
        else:  # special home
            home_commands = procedure.split("!")
            for hcmd in home_commands:
                goal = 0
                ax = hcmd[0]
                action = hcmd[1]
                if action == "a":
                    cmd = f"j{ax}a"
                elif action == "b":
                    cmd = f"j{ax}b"
                elif action == "h":
                    cmd = f"h{ax}"
                elif action == "g":
                    goal = round(float(hcmd[2::]) * self.steps_per_mm)
                    cmd = f"g{ax}{goal}"
                else:
                    raise ValueError(f"Malformed specialized homing procedure string at {hcmd} in {procedure}")
                answer = self.pcb.query(cmd)
                if answer != "":
                    raise ValueError(f"Error during specialized homing procedure. '{cmd}' rejected with {answer}")
                else:
                    if action in "hab":
                        self._wait_for_home_or_jog(ax, timeout=timeout - (time.time() - t0))
                        if action == "h":
                            this_len = self.len_axes_mm[ax]
                            if this_len == 0:
                                raise ValueError(f"Homing of axis {ax} resulted in measured length of zero.")
                            elif (allowed_deviation is not None) and (expected_lengths is not None):
                                el = expected_lengths[ax]
                                delta = abs(this_len - el)
                                if delta > allowed_deviation:
                                    raise ValueError(f"Error: Unexpected axis {ax} length. Found {this_len} [mm] but expected {el} [mm]")
                    elif action == "g":
                        self.goto({ax: goal}, timeout=timeout - (time.time() - t0), debug_prints=False)

    def _wait_for_home_or_jog(self, ax, timeout=300, debug_prints=False):
        t0 = time.time()
        poll_cmd = f"l{ax}"
        self.len_axes_mm[ax] = None
        while et := time.time() - t0 <= timeout:
            ax_len = self.pcb.expect_int(poll_cmd)
            if isinstance(ax_len, int) and (ax_len > 0):
                self.len_axes_mm[ax] = ax_len / self.steps_per_mm
                break
            time.sleep(self.poll_delay)
            if debug_prints == True:
                self.lg.debug(f'{ax}-l-b-{str(self.pcb.query(f"x{ax}18"))}')  # TSTEP register (0x12=18)  value
        else:
            raise ValueError(f"{timeout}s timeout exceeded while homing/jogging axis {ax}. Tried for {et}s.")

    # lower level (step based) position request function
    def _get_pos(self, ax):
        try:
            pcb_ans = self.pcb.query(f"r{ax}")
            rslt_pos = int(pcb_ans)
        except Exception:
            self.lg.debug(f"Warning: got unexpected _get_pos result: {pcb_ans}")
            rslt_pos = -1
        return rslt_pos

    def goto(self, targets_mm, timeout=300, debug_prints=False, blocking=True):
        """sends the stage some place. targets_mm is a dict with keys for axis numbers and vals for target mms"""
        t0 = time.time()

        targets_step = {}
        for ax, target_mm in targets_mm.items():  # convert mm to step values
            targets_step[ax] = round(target_mm * self.steps_per_mm)

        start_step = {}
        for ax, target_step in targets_step.items():  # initiate parallal motion and find start points
            start_step[ax] = self.send_g(ax, target_step)
        self.lg.debug(f"Motion to {targets_mm} started from {[val/self.steps_per_mm for key, val in start_step.items()]}")

        if blocking == True:  # wait for motion to complete
            time.sleep(self.poll_delay)
            for ax, target_step in targets_step.items():
                while et := time.time() - t0 <= timeout:
                    loc = self.send_g(ax, target_step)
                    if loc == target_step:
                        break
                    time.sleep(self.poll_delay)
                    if debug_prints == True:
                        self.lg.debug(f'{ax}-l-b-{str(self.pcb.query(f"x{ax}18"))}')  # TSTEP register (0x12=18)  value
                else:
                    if loc is not None:
                        self.lg.error(f"Motion on axis {ax} timed out while it was at {loc/self.steps_per_mm}")
                    if start_step[ax] is not None:
                        self.lg.error(f"While going from {start_step[ax]/self.steps_per_mm} to {target_step/self.steps_per_mm}")
                    raise ValueError(f"{timeout}s timeout exceeded while moving axis {ax}. Tried for {et}s.")

    def send_g(self, ax, target_step):
        """sends g (go to) cmd to axis controller"""
        if (pos := self.pcb.expect_int(f"r{ax}")) != target_step:  # first read pos (might not even need to send cmd)
            ax_len = self.pcb.expect_int(f"l{ax}")  # then read len (might not be allowed to move)
            if (ax_len == 0) or (ax_len == -1):  # length disaster detected
                if pos is not None:
                    self.lg.error(f"Axis {ax} was at {pos/self.steps_per_mm}mm while going to {target_step/self.steps_per_mm}mm")
                    self.lg.error(f"That's {(pos-target_step)/self.steps_per_mm}mm away")
                if ax_len == 0:
                    self.lg.error(f"Axis {ax} needs calibration.")
                if ax_len == -1:
                    self.lg.error(f"Axis {ax} can not be moved during the homing procedure.")
                raise ValueError(f"Failure moving axis {ax} to {target_step/self.steps_per_mm}")
            else:  # no length disaster
                cmd = f"g{ax}{target_step}"
                pcb_ans = None
                try:
                    pcb_ans = self.pcb.query(cmd)
                except Exception as e:
                    self.lg.debug(f"STAGE ISSUE: Exception in send_g for {cmd=} --> {pcb_ans=}")
                if pcb_ans != "":
                    self.lg.debug(f"STAGE ISSUE: Problem in send_g for {cmd=} --> {pcb_ans=}")
                if (not isinstance(ax_len, int)) or (ax_len < 0):
                    self.lg.debug(f"STAGE ISSUE: Problem reading axis length {ax_len=}")
        return pos

    # returns the stage's current position (a list matching the axes input)
    # axis is -1 for all available axes or a list of axes
    # returns None values for axes that could not be read
    def get_position(self):
        """returns a dict with keys axis number, val axis pos in mm"""
        result_mm = {}
        for ax in self.axes:
            get_cmd = f"r{ax}"
            answer = self.pcb.expect_int(get_cmd)
            if isinstance(answer, int) and (answer > 0):
                result_mm[ax] = answer / self.steps_per_mm
            else:
                self.lg.debug(f"STAGE ISSUE: Problem in get_position for {get_cmd=} --> {answer=}")
                result_mm[ax] = None
        return result_mm

    def estop(self, axes=-1):
        """
        Emergency stop of the driver. Unpowers the motor(s)
        """
        # do it thrice because it's important
        for i in range(3):
            self.pcb.query("b")
            for ax in self.axes:
                estop_cmd = f"b{ax}"
                self.pcb.query(estop_cmd)

    def close(self):
        pass

    def write_reg(self, ax, reg, val, check=True):
        """writes a value to a stepper driver register"""
        result = self.pcb.expect_empty(f"y{ax}{reg},{val}")

        if (check == True) and (result == True):
            read = self.read_reg(ax, reg)
            if read != val:
                result = False
                self.lg.debug(f"Attempt to program Axis {ax} register 0x{reg:X} with {val} (0x{val:X}, {val:032b}b), but got {read} (0x{read:X}, {read:032b}b)")

        return result

    def read_reg(self, ax, reg):
        """reads a value from a stepper driver register"""
        return self.pcb.expect_int(f"x{ax}{reg}")

    def reset(self, ax):
        """sends the reset command to an axis controller, ax is a string or int axis number counting up from 1"""
        timeout = 5  # seconds to wait for reset to complete
        t0 = time.time()
        success = self.pcb.expect_empty(f"t{ax}")
        if success == True:
            # poll for fwver to check for reset compete
            while (time.time() - t0) < timeout:
                time.sleep(0.3)
                stage_fw = self.pcb.query_nocheck(f"w{ax}")[0]
                if isinstance(stage_fw, str):
                    if "+" in stage_fw:
                        # see https://www.trinamic.com/fileadmin/assets/Products/ICs_Documents/TMC5130_datasheet_Rev1.18.pdf
                        self.write_reg(ax, self.TMC5130_XENC, 677)  # test/verify register programming
                        # self.write_reg(ax, self.TMC5130_V1, 0, check=False)  # disable some ramp generator stages
                        self.write_reg(ax, self.TMC5130_XENC, 678)  # test/verify register programming
                        break
            else:  # no break
                success = False
                self.lg.warning(f"Stage axis {ax} took too long to complete reset")
        return success


if __name__ == "__main__":
    from .pcb import pcb

    # motion test
    pcb_address = "10.46.0.239"
    steps_per_mm = 6400
    with pcb(pcb_address) as p:
        me = Us(p, spm=steps_per_mm)

        print("Connecting")
        result = me.connect()
        if result == 0:
            print("Connected!")
        else:
            raise (ValueError(f"Connection failed with {result}"))
        time.sleep(1)

        print("Homing")
        me.home()
        print(f"Homed!\nMeasured stage lengths = {me.len_axes_mm}")

        mid_mm = {}
        for ax in me.axes:
            mid_mm[ax] = me.len_axes_mm[ax] / 2
        print(f"GOingTO the middle of the stage: {mid_mm}")
        me.goto(mid_mm)
        print("Movement done.")
        time.sleep(1)

        print("Emergency Stopping")
        me.estop()
        print("E-stopped...")
        time.sleep(10)

        print("Testing failure handling")
        try:
            me.goto(mid_mm)
        except Exception as e:
            print(f"Got an exception: {e}")

        print("Homing")
        me.home()
        print(f"Homed!\nMeasured stage lengths = {me.len_axes_mm}")

        me.close()
        print("Test complete.")
