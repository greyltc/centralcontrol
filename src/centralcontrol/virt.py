import mpmath
import time
import numpy
import random
import inspect
import collections
import threading

import sys
import logging

# for logging directly to systemd journal if we can
try:
    import systemd.journal
except ImportError:
    pass


class Illumination(object):
    runtime = 60000
    intensity = 100
    barrier_timeout = 10  # s. wait at most this long for thread sync on light state change
    _current_state = False  # True if we believe the light is on, False if we believe it's off
    requested_state = False  # keeps track of what state we'd like the light to be in
    last_temps = None

    def __init__(self, *args, **kwargs):
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

        self.request_on = False
        self.requested_state = False
        self.barrier = threading.Barrier(1, action=self.set_state, timeout=self.barrier_timeout)  # thing that blocks threads until they're in sync

        self.lg.debug(f"{self.__class__} initialized.")

    @property
    def n_sync(self):
        """how many threads we need to wait for to synchronize"""
        return self.barrier.parties

    @n_sync.setter
    def n_sync(self, value):
        """
        update the number of threads we need to wait for to consider ourselves *NSYNC
        setting this while waiting for synchronization will raise a barrier broken error
        """

        self.barrier.abort()

        # the thing that blocks threads until they're in sync for a light state change
        self.barrier = threading.Barrier(value, action=self.set_state, timeout=self.barrier_timeout)

    @property
    def on(self):
        """
        query the light's current state
        (might differe than requested state)
        """
        return self._current_state

    @on.setter
    def on(self, value):
        """set true when you wish the light to be on and false if you want it to be off"""
        if isinstance(value, bool):
            if value != self._current_state:
                self.lg.debug(f"Request to change light state to {value}. Waiting for synchronization...")
                self.requested_state = value
                try:
                    draw = self.barrier.wait()
                    if draw == 0:  # we're the lucky winner!
                        self.lg.debug(f"Light state synchronization complete!")
                except threading.BrokenBarrierError as e:
                    # most likely a timeout
                    # could also be if the barrier was reset or aborted during the wait
                    # or if "action" (the call to change the light state) errored
                    raise ValueError(f"The light synchronization barrier was broken! {e}")
            else:
                # requested state matches actual state
                self.lg.debug(f"Light output is already {value}")
        else:
            self.lg.debug(f"Don't understand new light state request: {value=}")

    def set_state(self, force_state=None):
        """
        set illumination state based on self.requested_state
        should not be called directly (instead use the barrier-enabled on parameter)
        unless you call it with force_state True or False to bypass the barrier-based thread sync interface
        """
        call_state = None  # the state we'll be setting the light to
        ret = None
        if force_state is None:
            call_state = self.requested_state
            self.lg.debug(f"set_state {self.requested_state} called")
        elif isinstance(force_state, bool):
            call_state = force_state
        else:
            raise ValueError(f"New light state setting invalid: {call_state=}")

        if call_state:
            self.lg.debug("Virtual light turned on")
        else:
            self.lg.debug("Virtual light turned off")

        return ret

    def connect(self):
        self.lg.debug("Connected to virtual lightsource")
        return 0

    def set_recipe(self, recipe_name=None):
        self.lg.debug("Light engine recipe '{:}' virtually activated.".format(recipe_name))
        return 0

    def get_spectrum(self):
        self.lg.debug("Giving you a virtual spectrum")
        # dummy spectrum data to return (actual data from wavelabs)
        spec = ([330.0, 332.96875, 335.9375, 338.90625, 341.875, 344.84375, 347.8125, 350.78125, 353.75, 356.71875, 359.6875, 362.65625, 365.625, 368.59375, 371.5625, 374.53125, 377.5, 380.46875, 383.4375, 386.40625, 389.375, 392.34375, 395.3125, 398.28125, 401.25, 404.21875, 407.1875, 410.15625, 413.125, 416.09375, 419.0625, 422.03125, 425.0, 427.96875, 430.9375, 433.90625, 436.875, 439.84375, 442.8125, 445.78125, 448.75, 451.71875, 454.6875, 457.65625, 460.625, 463.59375, 466.5625, 469.53125, 472.5, 475.46875, 478.4375, 481.40625, 484.375, 487.34375, 490.3125, 493.28125, 496.25, 499.21875, 502.1875, 505.15625, 508.125, 511.09375, 514.0625, 517.03125, 520.0, 522.96875, 525.9375, 528.90625, 531.875, 534.84375, 537.8125, 540.78125, 543.75, 546.71875, 549.6875, 552.65625, 555.625, 558.59375, 561.5625, 564.53125, 567.5, 570.46875, 573.4375, 576.40625, 579.375, 582.34375, 585.3125, 588.28125, 591.25, 594.21875, 597.1875, 600.15625, 603.125, 606.09375, 609.0625, 612.03125, 615.0, 617.96875, 620.9375, 623.90625, 626.875, 629.84375, 632.8125, 635.78125, 638.75, 641.71875, 644.6875, 647.65625, 650.625, 653.59375, 656.5625, 659.53125, 662.5, 665.46875, 668.4375, 671.40625, 674.375, 677.34375, 680.3125, 683.28125, 686.25, 689.21875, 692.1875, 695.15625, 698.125, 701.09375, 704.0625, 707.03125, 710.0, 712.96875, 715.9375, 718.90625, 721.875, 724.84375, 727.8125, 730.78125, 733.75, 736.71875, 739.6875, 742.65625, 745.625, 748.59375, 751.5625, 754.53125, 757.5, 760.46875, 763.4375, 766.40625, 769.375, 772.34375, 775.3125, 778.28125, 781.25, 784.21875, 787.1875, 790.15625, 793.125, 796.09375, 799.0625, 802.03125, 805.0, 807.96875, 810.9375, 813.90625, 816.875, 819.84375, 822.8125, 825.78125, 828.75, 831.71875, 834.6875, 837.65625, 840.625, 843.59375, 846.5625, 849.53125, 852.5, 855.46875, 858.4375, 861.40625, 864.375, 867.34375, 870.3125, 873.28125, 876.25, 879.21875, 882.1875, 885.15625, 888.125, 891.09375, 894.0625, 897.03125, 900.0, 902.96875, 905.9375, 908.90625, 911.875, 914.84375, 917.8125, 920.78125, 923.75, 926.71875, 929.6875, 932.65625, 935.625, 938.59375, 941.5625, 944.53125, 947.5, 950.46875, 953.4375, 956.40625, 959.375, 962.34375, 965.3125, 968.28125, 971.25, 974.21875, 977.1875, 980.15625, 983.125, 986.09375, 989.0625, 992.03125, 995.0, 997.96875, 1000.9375, 1003.90625, 1006.875, 1009.84375, 1012.8125, 1015.78125, 1018.75, 1021.71875, 1024.6875, 1027.65625, 1030.625, 1033.59375, 1036.5625, 1039.53125, 1042.5, 1045.46875, 1048.4375, 1051.40625, 1054.375, 1057.34375, 1060.3125, 1063.28125, 1066.25, 1069.21875, 1072.1875, 1075.15625, 1078.125, 1081.09375, 1084.0625, 1087.03125], [98.0546875, 100.17578125, 95.76953125, 78.8203125, 80.2421875, 57.89453125, 71.53515625, 77.41796875, 84.69921875, 187.91796875, 456.91015625, 624.26171875, 969.203125, 1076.01171875, 1076.0078125, 1025.87890625, 1020.41796875, 1321.7734375, 1592.00390625, 2103.54296875, 2251.84375, 2204.5, 1814.2734375, 1622.6484375, 1575.57421875, 1850.40234375, 3117.87109375, 5016.8515625, 5896.8046875, 6827.8515625, 6745.66015625, 5732.13671875, 4331.87109375, 3738.55859375, 3003.42578125, 2839.27734375, 2900.2109375, 3228.66015625, 3425.2109375, 3830.2578125, 4071.1875, 4774.875, 5810.08984375, 6380.40625, 7377.78515625, 7696.40234375, 7734.125, 7033.515625, 6504.1875, 5393.9921875, 4526.1796875, 4217.46875, 3834.640625, 3731.40625, 3661.6171875, 3711.29296875, 3757.7890625, 3817.328125, 3890.4375, 3929.984375, 3995.1796875, 4076.4609375, 4133.16796875, 4284.5234375, 4337.65625, 4480.140625, 4587.58203125, 4642.078125, 4675.7421875, 4673.59765625, 4629.9609375, 4509.80078125, 4374.8828125, 4311.71484375, 4239.328125, 4203.5625, 4186.1796875, 4161.9453125, 4174.734375, 4161.8125, 4162.58203125, 4127.1953125, 4066.10546875, 4036.1875, 3940.84375, 3828.80078125, 3734.859375, 3701.17578125, 3686.43359375, 3711.640625, 3743.81640625, 3826.92578125, 3978.0859375, 4136.80078125, 4204.60546875, 4373.23828125, 4500.02734375, 4561.453125, 4559.39453125, 4515.13671875, 4414.08984375, 4254.19921875, 4022.4375, 3912.83203125, 3684.09765625, 3480.7421875, 3288.94921875, 3216.64453125, 3135.68359375, 3125.80078125, 3139.4921875, 3231.31640625, 3354.73828125, 3462.640625, 3606.38671875, 3713.359375, 3726.71875, 3613.23046875, 3387.62890625, 3261.82421875, 2956.87890625, 2683.16796875, 2446.234375, 2284.95703125, 2143.7265625, 1982.5390625, 1820.71875, 1746.765625, 2928.8515625, 2942.18359375, 3102.0, 3242.4140625, 3426.26171875, 3771.65625, 3911.2265625, 3997.08984375, 3990.8203125, 3898.703125, 3619.265625, 3469.5859375, 3349.55859375, 3285.46875, 3345.66015625, 3464.5859375, 3810.109375, 3967.140625, 4107.10546875, 4071.45703125, 3959.125, 3614.41015625, 3461.109375, 3341.30859375, 3423.0078125, 3593.01953125, 4164.15234375, 4536.31640625, 5394.65625, 5780.234375, 6162.02734375, 6084.484375, 5854.09375, 4943.6015625, 4372.0078125, 3224.453125, 2740.14453125, 2006.35546875, 1751.8046875, 1526.96484375, 1216.421875, 1117.515625, 982.0859375, 946.93359375, 914.21875, 908.0234375, 957.5234375, 992.9921875, 1037.43359375, 1043.625, 1006.6171875, 969.9296875, 884.67578125, 836.7265625, 735.4453125, 706.359375, 646.140625, 619.98828125, 582.68359375, 557.1484375, 509.89453125, 487.1875, 461.7734375, 453.77734375, 417.671875, 402.09375, 401.5390625, 419.546875, 428.23046875, 418.984375, 424.671875, 434.2890625, 424.921875, 416.08203125, 413.94921875, 393.015625, 387.98828125, 405.71875, 383.87890625, 381.69140625, 377.21484375, 380.07421875, 347.76953125, 344.5234375, 339.08984375, 326.09375, 313.95703125, 323.05078125, 296.8046875, 272.12109375, 287.52734375, 253.32421875, 226.9921875, 219.03125, 215.61328125, 199.65625, 171.21875, 173.31640625, 182.72265625, 167.40625, 154.19140625, 165.3203125, 175.640625, 172.9140625, 154.44140625, 167.83984375, 169.15625, 163.4765625, 144.01953125, 136.86328125, 135.640625, 142.90625, 123.91015625, 107.26171875, 110.2421875, 114.65234375, 98.17578125, 86.140625, 74.7734375, 90.97265625, 76.65625, 71.09375, 67.83203125, 69.8984375, 59.78515625, 46.9375, 32.2421875, 30.9765625])
        wls = spec[0]
        counts = spec[1]
        scaled_counts = [count * self.intensity / 100 for count in counts]
        return (wls, scaled_counts)

    def get_run_status(self):
        if self._current_state is True:
            ret = "running"
        else:
            ret = "finished"
        return ret

    def disconnect(self, *args, **kwargs):
        pass

    def set_runtime(self, ms):
        self.runtime = ms
        return 0

    def get_runtime(self):
        return self.runtime

    def set_intensity(self, percent):
        self.intensity = percent
        return 0

    def get_intensity(self):
        return self.intensity

    def get_temperatures(self, *args, **kwargs):
        temp = [25.3, 17.3]
        self.last_temps = temp
        return temp


class pcb(object):
    is_virtual = True
    virt_speed = 300  # virtual movement speed in mm per sec
    virt_motion_setup = False  # to track if we're prepared to virtualize motion
    firmware_version = "1.0.0"
    detected_axes = ["1", "2", "3"]
    detected_muxes = ["A"]

    def __init__(self, *args, **kwargs):
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

        self._votes_needed = 1
        self.on_votes = collections.deque([], maxlen=self._votes_needed)

        self.lg.debug(f"{self.__class__} initialized.")

        if "expected_muxes" in kwargs:
            self.detected_muxes = kwargs["expected_muxes"]
        pass

    def prepare_virt_motion(self, spm, el):
        self.spm = spm
        self.vs = self.virt_speed * spm  # convert to mm/s
        self.el = el
        self.ml = {}
        self.homing = {}
        self.jogging = {}
        self.goingto = {}
        self.pos = {}
        self.goal = {}
        self.home_done_time = {}
        self.jog_done_time = {}
        self.goto_done_time = {}
        self.detected_axes = []
        for key, val in el.items():
            self.detected_axes.append(key)
            self.homing[key] = False
            self.jogging[key] = False
            self.goingto[key] = False
            self.ml[key] = round(self.el[key] * spm)
            self.pos[key] = round(self.ml[key] / 2)
            self.goal[key] = round(self.ml[key] / 2)
            self.home_done_time[key] = time.time()
            self.jog_done_time[key] = time.time()
            self.goto_done_time[key] = time.time()
        self.virt_motion_setup = True

    def __enter__(self):
        return self

    def __exit__(self, *args, **kwargs):
        pass

    def probe_axes(self):
        pass
        # if len(self.el) == 1:
        #    self.detected_axes = ["1"]
        # elif len(self.el) == 2:
        #    self.detected_axes = ["1", "2"]
        # elif len(self.el) == 3:
        #    self.detected_axes = ["1", "2", "3"]

    def set_mux(self, mux_setting):
        return ""

    def expect_int(self, cmd, tries=1):
        return int(self.query(cmd))

    def query(self, cmd):
        self.lg.debug(f"Virtual CALL. Class={type(self).__name__}. function={inspect.currentframe().f_code.co_name}. cmd={cmd}")
        if self.virt_motion_setup == True:
            # now let's do timing related motion calcs
            now = time.time()
            for key, val in self.el.items():
                if (now > self.home_done_time[key]) and (self.homing[key] == True):  # homing for this axis is done
                    self.ml[key] = round(self.el[key] * self.spm)
                    self.homing[key] = False
                    self.pos[key] = round(0.95 * self.ml[key])

                if self.goingto[key] == True:
                    if now > self.goto_done_time[key]:
                        self.pos[key] = round(self.goal[key])
                        self.goingto[key] = False
                    else:  # axis is in motion from goto, so we must calculate where we are
                        time_remaining = self.goto_done_time[key] - now
                        distance_remaining = time_remaining * self.virt_speed
                        if self.goal[key] < self.pos[key]:  # moving reverse
                            self.pos[key] = round(self.goal[key] + distance_remaining)
                        else:
                            self.pos[key] = round(self.goal[key] - distance_remaining)

                if (now > self.jog_done_time[key]) and (self.jogging[key] == True):  # jogging for this axis is done
                    self.ml[key] = 0
                    self.jogging[key] = False

        # now we're ready to parse the command and respond to it
        if (len(cmd) == 2) and (cmd[0] == "l"):  # axis length request
            axi = cmd[1]
            return str(self.ml[axi])
        elif (cmd == "iv") or (cmd == "eqe"):  # relay selection (must be before ax driver status byte cmd below)
            return ""
        elif cmd[0] == "h":  # axis home request
            if len(cmd) == 2:
                operate_on = [cmd[1]]
            else:
                operate_on = self.detected_axes
            for ax in operate_on:
                axi = ax
                self.home_done_time[axi] = time.time() + 2 * self.el[axi] * self.spm / self.vs
                self.ml[axi] = -1
                self.homing[axi] = True
            return ""
        elif (len(cmd) == 3) and (cmd[0] == "j"):  # axis jog request
            axi = cmd[1]
            direction = cmd[2]
            # 'r' command while jogging gives error 102, so we can just set pos now
            if direction == "a":
                self.pos[axi] = 0
            else:
                self.pos[axi] = round(self.el[axi] * self.spm)
            self.jog_done_time[axi] = time.time() + self.el[axi] * self.spm / self.vs
            self.jogging[axi] = True
            self.ml[axi] = -1
            return ""
        elif (len(cmd) == 2) and (cmd[0] == "i"):  # axis driver status byte
            return "11111111"
        elif cmd[0] == "g":  # goto command
            axi = cmd[1]
            self.goingto[axi] = True
            self.goal[axi] = round(float(cmd[2::]))
            self.goto_done_time[axi] = time.time() + abs(self.goal[axi] - self.pos[axi]) / self.vs
            return ""
        elif (len(cmd) == 2) and (cmd[0] == "r"):  # axis position request
            axi = cmd[1]
            if (self.homing[axi] == True) or (self.jogging[axi] == True):
                return "ERROR 102"
            else:
                return str(self.pos[axi])
        elif cmd[0] == "b":  # estop
            if self.virt_motion_setup == True:
                if len(cmd) == 2:
                    to_estop = [cmd[1]]
                else:
                    to_estop = self.detected_axes
                for ax in to_estop:
                    axi = ax
                    self.ml[axi] = 0
                    self.goto_done_time[axi] = time.time()
                    self.goal[axi] = self.pos[axi]
                    self.home_done_time[axi] = time.time()
                    self.jog_done_time[axi] = time.time()
                    self.goto_done_time[axi] = time.time()
                    self.homing[axi] = False
                    self.jogging[axi] = False
                    self.goingto[axi] = False
            return ""
        elif cmd[0] == "s":  # pixel selection
            return ""
        elif cmd[0] == "w":  # firmware request
            return "virtual FW version 5"
        else:
            return "Command virtually unsupported"


class smu(object):
    """virtual smu class"""

    idn: str
    nplc = 1
    ccheck = False
    killer = None
    print_sweep_deets: bool = False

    def __init__(self, *args, **kwargs):
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

        self.idn = "Virtual Sourcemeter"

        self._votes_needed = 1
        self.on_votes = collections.deque([], maxlen=self._votes_needed)

        self.lg.debug(f"{self.__class__} setup complete.")

        self.t0 = time.time()
        self.measurementTime = 0.01  # [s] the time it takes the simulated sourcemeter to make a measurement

        if "killer" in kwargs:
            self.killer = kwargs["killer"]
        else:
            self.killer = threading.Event()

        # these will get updated externally as needed
        self.area = 1  # cm^2
        # TODO: add dark area
        self.compliance = 1  # A
        self.dark = False  # if we're in the dark, do computations with Iph = 0

        # here we choose some numbers for our simulated solar cell model
        self.Iphd = 23  # photocurrent density, in mA/cm^2 (where cm^2 is for illuminated area)
        self.I0d = 1e-12  # dark current density in mA/cm^2 (where cm^2 is for physical device area)
        self.n = 1.3  # diode ideality factor
        self.Rsa = 1.8  # arial series resistance [ohm*cm^2] (where cm^2 is for physical device area)
        self.Rsha = 8e2  # arial shunt resistance [ohm*cm^2] (where cm^2 is for physical device area)

        self.cellTemp = 29  # degC
        self.T = 273.15 + self.cellTemp  # cell temp in K
        self.K = 1.3806488e-23  # boltzman constant
        self.q = 1.60217657e-19  # electron charge
        self.Vth = mpmath.mpf(self.K * self.T / self.q)  # thermal voltage ~26mv
        self.V = 0  # voltage across device
        self.I = 0  # current through device
        self.updateCurrent()

        # for sweeps:
        self.sweepMode = False
        self.nPoints = 1001
        self.sweepStart = 1
        self.sweepEnd = 0

        self.status = 0
        self.four88point1 = True
        self.auto_ohms = False

        if "print_sweep_deets" in kwargs:
            self.print_sweep_deets = kwargs["print_sweep_deets"]

    def connect(self):
        self.lg.debug(f"{self.__class__} initalized.")
        return

    def __del__(self, *args, **kwargs):
        return

    def setNPLC(self, *args, **kwargs):
        self.nplc = args[0]

    def getNPLC(self, *args, **kwargs):
        return self.nplc

    def disconnect(self, *args, **kwargs):
        return

    def setWires(self, *args, **kwargs):
        return

    def setTerminals(self, *args, **kwargs):
        return

    def updateSweepStart(self, startVal):
        self.sweepStart = startVal

    def updateSweepStop(self, stopVal):
        self.sweepEnd = stopVal

    def setupDC(self, sourceVoltage=True, compliance=0.04, setPoint=0, senseRange="f", auto_ohms=False):
        self.compliance = compliance
        if auto_ohms == True:
            self.auto_ohms = True
        else:
            self.auto_ohms = False
            if sourceVoltage:
                src = "voltage"
                snc = "current"
            else:
                src = "current"
                snc = "voltage"
            self.src = src
            self.write(":source:{:s} {:0.6f}".format(self.src, setPoint))
            self.sweepMode = False
        return

    def setupSweep(self, sourceVoltage=True, compliance=0.04, nPoints=101, stepDelay=-1, start=0, end=1, senseRange="f"):
        """setup for a sweep operation"""
        self.compliance = compliance
        # sm = self.sm
        if sourceVoltage:
            src = "voltage"
            snc = "current"
        else:
            src = "current"
            snc = "voltage"
        self.src = src
        self.nPoints = nPoints
        self.sweepMode = True
        self.sweepStart = start
        self.sweepEnd = end
        self.dV = abs(float(self.query_values(":source:voltage:step?")))

    def setSource(self, outVal):
        self.write(":source:{:s} {:.6f}".format(self.src, outVal))

    def outOn(self, on=True):
        return

    def opc(self, *args, **kwargs):
        return

    # the device is open circuit
    def openCircuitEvent(self):
        self.I = 0
        Rsh = self.Rsha / self.area
        n = self.n
        I0 = self.I0d * self.area / 1000
        if self.dark == True:
            Iph = 0
        else:
            Iph = self.Iphd * self.area / 1000
        Vth = self.Vth
        if Rsh < float("inf"):
            Voc = Rsh * (I0 + Iph) - Vth * n * mpmath.lambertw(I0 * Rsh * mpmath.exp(Rsh * (I0 + Iph) / (Vth * n)) / (Vth * n))
        else:
            Voc = Vth * n * mpmath.log((I0 + Iph) / I0)
        self.V = float(mpmath.fabs(Voc)) * float(mpmath.sign(mpmath.re(Voc)))
        self.I = 0

    # recompute device current
    def updateCurrent(self):
        Rs = self.Rsa / self.area
        Rsh = self.Rsha / self.area
        n = self.n
        I0 = self.I0d * self.area / 1000
        if self.dark == True:
            Iph = 0
        else:
            Iph = self.Iphd * self.area / 1000
        Vth = self.Vth
        V = self.V
        if (Rs > 0) and (Rsh < float("inf")):  # both resistors active
            I = (Rs * (I0 * Rsh + Iph * Rsh - V) - Vth * n * (Rs + Rsh) * mpmath.lambertw(I0 * Rs * Rsh * mpmath.exp((Rs * (I0 * Rsh + Iph * Rsh - V) / (Rs + Rsh) + V) / (Vth * n)) / (Vth * n * (Rs + Rsh)))) / (Rs * (Rs + Rsh))
        elif (Rs <= 0) and (Rsh < float("inf")):  # Rs is perfect (0 ohm)
            I = -I0 * mpmath.exp(V / (Vth * n)) + I0 + Iph - V / Rsh
        elif (Rs > 0) and (Rsh == float("inf")):  # Rsh is perfect (inf ohm)
            I = (Rs * (I0 + Iph) - Vth * n * mpmath.lambertw(I0 * Rs * mpmath.exp((Rs * (I0 + Iph) + V) / (Vth * n)) / (Vth * n))) / Rs
        else:  # no resistive losses
            I = -I0 * mpmath.exp(V / (Vth * n)) + I0 + Iph
        self.I = float(mpmath.fabs(I)) * float(mpmath.sign(mpmath.re(I)))
        # simulate the SMU hitting compliance
        if abs(self.I) > abs(self.compliance):  # check if we're over the current limit
            # set I to correct compliance limit
            if self.I >= 0:
                self.I = abs(self.compliance)
            else:
                self.I = -1 * abs(self.compliance)
            # then figure out what V should be there
            if (Rs > 0) and (Rsh < float("inf")):  # both resistors active
                peggedV = -self.I * Rs - self.I * Rsh + I0 * Rsh + Iph * Rsh - Vth * n * mpmath.lambertw(I0 * Rsh * mpmath.exp(Rsh * (-self.I + I0 + Iph) / (Vth * n)) / (Vth * n))
            elif (Rs <= 0) and (Rsh < float("inf")):  # Rs is perfect (0 ohm)
                peggedV = Rsh * (-self.I + I0 + Iph) - Vth * n * mpmath.lambertw(I0 * Rsh * mpmath.exp(Rsh * (-self.I + I0 + Iph) / (Vth * n)) / (Vth * n))
            elif (Rs > 0) and (Rsh == float("inf")):  # Rsh is perfect (inf ohm)
                peggedV = Vth * n * mpmath.log((-self.I + I0 + Iph) * mpmath.exp(-self.I * Rs / (Vth * n)) / I0)
            else:  # no resistive losses
                peggedV = Vth * n * mpmath.log((-self.I + I0 + Iph) / I0)
            self.V = float(mpmath.fabs(peggedV)) * float(mpmath.sign(mpmath.re(peggedV)))

        # change from cell's POV to SMU's POV
        self.I = self.I * -1

    def write(self, command):
        if ":source:current " in command:
            currentVal = float(command.split(" ")[1])
            if currentVal == 0:
                self.openCircuitEvent()
            else:
                raise ValueError("Can't do currents other than zero right now!")
        elif command == ":source:voltage:mode sweep":
            self.sweepMode = True
        elif command == ":source:voltage:mode fixed":
            self.sweepMode = False
        elif ":source:sweep:points " in command:
            self.nPoints = int(command.split(" ")[1])
        elif ":source:voltage:start " in command:
            self.sweepStart = float(command.split(" ")[1])
        elif ":source:voltage:stop " in command:
            self.sweepEnd = float(command.split(" ")[1])
        elif ":source:voltage " in command:
            self.V = float(command.split(" ")[1])
            self.updateCurrent()

    def query_ascii_values(self, command):
        return self.query_values(command)

    def read(self):
        return self.query_values("READ?")

    def query_values(self, command):
        if command == "READ?":
            if self.sweepMode:
                sweepArray = []
                voltages = numpy.linspace(self.sweepStart, self.sweepEnd, self.nPoints)
                for i in range(len(voltages)):
                    self.V = voltages[i]
                    self.updateCurrent()
                    time.sleep(self.measurementTime)
                    if self.auto_ohms == False:
                        measurementLine = list([self.V, self.I, time.time() - self.t0, self.status])
                    else:
                        measurementLine = list([self.V, self.I, self.V / self.I, time.time() - self.t0, self.status])
                    sweepArray.append(measurementLine)
                self.last_sweep_time = sweepArray[-1][2] - sweepArray[0][2]
                self.lg.debug(f"Sweep duration = {self.last_sweep_time} s")
                return sweepArray
            else:  # non sweep mode
                time.sleep(self.measurementTime)
                if self.auto_ohms == False:
                    measurementLine = list([self.V, self.I, time.time() - self.t0, self.status])
                else:
                    ohm = 700 + random.random() * 100
                    measurementLine = list([self.V, self.I, ohm, time.time() - self.t0, self.status])
                return [measurementLine]
        elif command == ":source:voltage:step?":
            dV = (self.sweepEnd - self.sweepStart) / self.nPoints
            return dV
        elif command == ":source:current:step?":
            dI = (self.sweepEnd - self.sweepStart) / self.nPoints
            return dI
        else:
            raise ValueError("What?")

    def measureUntil(self, t_dwell=float("Infinity"), measurements=float("Infinity"), cb=lambda x: None):
        """Meakes measurements until termination conditions are met
        supports a callback after every measurement
        returns a queqe of measurements
        """
        i = 0
        t_end = time.time() + t_dwell
        q = []
        while (i < measurements) and (time.time() < t_end) and (not self.killer.is_set()):
            i = i + 1
            measurement = self.measure()[0]
            q.append(measurement)
            cb(measurement)
        return q

    def measure(self, nPoints=1):
        if self.auto_ohms == False:
            m_len = 4
        else:
            m_len = 5
        vals = self.query_values("READ?")

        if len(vals) > 1:
            first_element = vals[0]
            last_element = vals[-1]
            if m_len == 4:
                t_start = first_element[2]
                t_end = last_element[2]
            elif m_len == 5:
                t_start = first_element[3]
                t_end = last_element[3]
            else:
                t_start = 0
                t_end = 0
            v_start = first_element[0]
            v_end = last_element[0]
            self.last_sweep_time = t_end - t_start
            stats_string = f"Sweep stats: avg. step voltage|duration|avg. point time|avg. rate-->{(v_start-v_end)/len(vals)*1000:0.2f}mV|{self.last_sweep_time:0.2f}s|{self.last_sweep_time/len(vals)*1000:0.0f}ms|{(v_start-v_end)/self.last_sweep_time:0.3f}V/s"
            if self.print_sweep_deets == True:
                self.lg.info(stats_string)
            else:
                self.lg.debug(stats_string)

        return vals

    def set_ccheck_mode(self, mode):
        self.ccheck = mode

    def contact_check(self, *args, **kwargs):
        return True

    def close(self):
        self.lg.debug(f"{self.__class__} closed.")
