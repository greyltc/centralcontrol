import numpy
import time
import random
import typing
from collections import deque
from threading import Event as tEvent
from multiprocessing.synchronize import Event as mEvent
from centralcontrol.sourcemeter import SourcemeterAPI as smapi

try:
    from centralcontrol.logstuff import get_logger as getLogger
except:
    from logging import getLogger


class mppt:
    """
    Maximum power point tracker class
    """

    # external signaler to end the mppt prematurely
    killer: mEvent | tEvent

    Voc: float | None = None
    Isc: float | None = None
    Mmpp: tuple[float, float, float] | None = None  # measurement from max power point
    Vmpp: float | None = None  # voltage at max power point
    Impp: float | None = None  # current at max power point
    Pmax: float | None = None  # power at max power point (for keeping track of voc and isc)
    area: float | None = None

    # under no circumstances should we violate this
    absolute_current_limit = 0.1  # always safe default

    t0: float | None = None  # the time we started the mppt algorithm

    # if we're forced to guess Voc or Vmpp, assume that vmpp is this fraction of Voc
    voc_vmpp_guess_ratio = 0.7

    sm: smapi

    def __init__(self, sm: smapi, absolute_current_limit=0.1):
        self.sm = sm
        self.killer = sm.killer

        self.lg = getLogger(".".join([__name__, type(self).__name__]))  # setup logging
        self.absolute_current_limit = abs(absolute_current_limit)
        self.lg.debug(f"Initialized.")

    def reset(self):
        self.area = None
        self.Voc = None
        self.Isc = None
        self.Mmpp = None  # measurement from max power point
        self.Vmpp = None  # voltage at max power point
        self.Impp = None  # current at max power point
        self.Pmax = None  # power at max power point

        self.t0 = None  # the time we started the mppt algorithm

    def register_curve(self, vector, light=True):
        """
        registers an IV curve with the max power point tracker
        given a list of raw measurements, figures out which one produced the highest power
        updates some values for mppt if light=True
        """
        v = numpy.array([e[0] for e in vector])
        i = numpy.array([e[1] for e in vector])
        t = numpy.array([e[2] for e in vector])
        p = v * i * -1
        iscIndex = numpy.argmin(abs(v))
        Isc = i[iscIndex]
        vocIndex = numpy.argmin(abs(i))
        Voc = v[vocIndex]
        maxIndex = numpy.argmax(p)
        Vmpp = v[maxIndex]
        Pmax = p[maxIndex]
        Impp = i[maxIndex]
        Tmpp = t[maxIndex]
        if light is True:  # this was from a light i-v curve
            self.lg.debug(f"MPPT IV curve inspector investigating new light curve params: {(Pmax, Vmpp, Impp, Voc, Isc)}")
            if (self.Pmax is None) or (Pmax > self.Pmax):
                if self.Pmax is None:
                    because = "there was no previous one."
                else:
                    because = f"we beat the old max power value, {Pmax} > f{self.Pmax} [W]"
                self.lg.debug(f"New refrence IV curve found for MPPT algo because {because}")
                self.Vmpp = Vmpp
                self.Impp = Impp
                self.Pmax = Pmax
                self.Mmpp = (Vmpp, Impp, Tmpp)  # store off measurement value for this one
                self.lg.debug(f"V_mpp = {self.Vmpp}[V]\nI_mpp = {self.Impp}[A]\nP_max = {self.Pmax}[W]")
                if (min(v) <= 0) and (max(v) >= 0):  # if we had data on both sizes of 0V, then we can estimate Isc
                    self.Isc = Isc
                    self.lg.debug(f"I_sc = {self.Isc}[A]")
                if (min(i) <= 0) and (max(i) >= 0):  # if we had data on both sizes of 0A, then we can estimate Voc
                    self.Voc = Voc
                    self.lg.debug(f"V_oc = {self.Voc}[V]")
        # returns maximum power[W], Vmpp, Impp and the index
        return (Pmax, Vmpp, Impp, maxIndex)

    def launch_tracker(self, duration: float = 30.0, callback: typing.Callable[[list[tuple[float, float, float, int]]], None] = lambda x: None, NPLC=-1, voc_compliance=3, i_limit=0.1, extra="basic://7:10:10", area=1):
        """
        general function to call begin a max power point tracking algorithm
        duration given in seconds, optionally calling callback function on each measurement point
        """
        m = []  # list holding mppt measurements
        self.t0 = time.time()  # start the mppt timer
        self.area = area

        if abs(i_limit) > abs(self.absolute_current_limit):
            i_limit = abs(self.absolute_current_limit)

        if NPLC != -1:
            self.sm.setNPLC(NPLC)

        dunnos = []  # list of params we need that we don't know
        ssvocs = []  # data from required ssvoc measurement to learn Voc
        if self.Voc is None:
            dunnos.append("V_oc")
        if self.Vmpp is None:
            dunnos.append("V_mpp")

        if len(dunnos) > 0:
            self.lg.warning(f"The MPP tracker is starting up without knowing {' and '.join(dunnos)}")
            if len(dunnos) == 2:  # we don't know anything so we have to measure Voc
                voc_dwell_time = 1.0  # amount of time to wait for Voc to settle
                self.sm.setupDC(sourceVoltage=False, compliance=voc_compliance, setPoint=0, senseRange="a")
                ssvocs = self.sm.measureUntil(t_dwell=voc_dwell_time)
                self.Voc = ssvocs[-1][0]  # take the last value as Voc
                self.lg.warning(f"Discovered that V_oc =~ {self.Voc} [V] via a {voc_dwell_time:.1f}s voltage measurement")
                dunnos.remove("V_oc")
            if ("V_oc" in dunnos) and self.Vmpp:
                self.Voc = 1 / self.voc_vmpp_guess_ratio * self.Vmpp
                self.lg.warning(f"Inferred that V_oc =~ {self.Voc} [V] from {1/self.voc_vmpp_guess_ratio:.1f} times V_mpp")
            if ("V_mpp" in dunnos) and self.Voc:
                self.Vmpp = self.voc_vmpp_guess_ratio * self.Voc
                self.lg.warning(f"Inferred that V_mpp =~ {self.Vmpp} [V] from {self.voc_vmpp_guess_ratio:.1f} times V_oc")

        # we must know Vmpp and Voc to continue
        assert self.Vmpp is not None
        assert self.Voc is not None

        # handle a killer kill
        if self.killer.is_set():
            self.lg.debug("Killed by killer.")
            return (m, ssvocs)

        # get the smu ready for doing the mppt
        self.sm.setupDC(sourceVoltage=True, compliance=i_limit, setPoint=self.Vmpp, senseRange="f")

        # lock the smu to the device's power quadrant
        if self.Vmpp >= 0:
            self.quadrant_lock = True  # lock mppt voltage to be >0
        else:
            self.quadrant_lock = False  # lock mppt voltage to be <0

        # run a tracking algorithm
        extra_split = extra.split(sep="://", maxsplit=1)
        algo = extra_split[0]
        params = extra_split[1]
        if algo == "basic":
            if len(params) == 0:  #  use defaults
                m += self.really_dumb_tracker(duration, start_voltage=self.Vmpp, callback=callback)
            else:
                params = params.split(":")
                if len(params) != 3:
                    raise (ValueError("MPPT configuration failure, Usage: --mppt-params basic://[degrees]:[dwell]:[sweep_delay_ms]"))
                params = [float(f) for f in params]
                m += self.really_dumb_tracker(duration, start_voltage=self.Vmpp, callback=callback, dAngleMax=params[0], dwell_time=params[1], sweep_delay_ms=params[2])
        elif algo == "spo":
            if len(params) == 0:  #  use defaults
                m += self.spo(duration, callback=callback)
            else:
                raise (ValueError("MPPT configuration failure, Usage: spo://"))
        elif algo in ["gd", "snaith"]:
            if algo == "snaith":
                do_snaith = True
            else:
                do_snaith = False
            if len(params) == 0:  # use defaults
                m += self.gradient_descent(duration, start_voltage=self.Vmpp, snaith_mode=do_snaith, callback=callback)
            else:
                params = params.split(":")
                if len(params) != 10:
                    raise (ValueError("MPPT configuration failure, Usage: gd://[alpha]:[min_step]:[NPLC]:[delayms]:[max_step]:[momentum]:[delta_zero]:[jump_percent]:[jump_period]:[time_scale]"))
                params = [float(f) for f in params]
                m += self.gradient_descent(duration, start_voltage=self.Vmpp, callback=callback, alpha=params[0], min_step=params[1], NPLC=params[2], delay_ms=params[3], snaith_mode=do_snaith, max_step=params[4], momentum=params[5], delta_zero=params[6], jump_percent=params[7], jump_period=params[8], time_scale=(params[9] == 1))
        else:
            self.lg.debug(f"WARNING: MPPT algorithm {algo} not understood, not doing max power point tracking")

        run_time = time.time() - self.t0
        self.lg.debug("Final value seen by the max power point tracker after running for {:.1f} seconds is".format(run_time))
        if (self.Vmpp) and (self.Impp):
            self.lg.debug("{:0.4f} mW @ {:0.2f} mV and {:0.2f} mA".format(self.Vmpp * self.Impp * 1000 * -1, self.Vmpp * 1000, self.Impp * 1000))
        return (m, ssvocs)

    def spo(self, duration, callback=lambda x: None):
        """just runs with a fixed start voltage"""
        self.lg.warning("spo:// does not attempt to hold maximum power point")

        data = self.sm.measureUntil(t_dwell=duration, cb=callback)

        # take whatever the most recent readings were to be the mppt
        self.Vmpp = data[0][0]
        self.Impp = data[0][1]

        return data

    def gradient_descent(self, duration: float, start_voltage: float, callback: typing.Callable[[list[tuple[float, float, float, int]]], None] = lambda x: None, alpha: float = 1.5, min_step: float = 0.001, NPLC: float = 10.0, snaith_mode: bool = False, delay_ms: float = 500.0, max_step: float = 0.1, momentum: float = 0.1, delta_zero: float = 0.01, jump_percent: float = 10.0, jump_period: float = 0.0, time_scale: bool = True):
        """
        gradient descent MPPT algorithm
        alpha is the "learning rate"
        min_step is the minimum voltage step size the algorithm will be allowed to take
        delay is the number of ms to wait between setting the voltage and making a measurement
        """

        # we must know these to continue
        assert self.t0 is not None
        assert self.Voc is not None

        # snaith mode constants
        snaith_pre_soak_t = 15
        snaith_post_soak_t = 3

        if NPLC != -1:
            self.sm.setNPLC(NPLC)

        self.lg.debug("===Starting up gradient descent maximum power point tracking algorithm===")
        self.lg.debug(f"Learning rate (alpha) = {alpha}")
        self.lg.debug(f"Using time scaling = {time_scale}")
        self.lg.debug(f"V_initial = {start_voltage} [V]")
        self.lg.debug(f"delta_zero = {delta_zero} [V]")  # first step
        self.lg.debug(f"momentum = {momentum}")
        self.lg.debug(f"jump percentage of Voc = {jump_percent}%")
        self.lg.debug(f"jump period = {jump_period} [s]")
        self.lg.debug(f"Smallest step (min_step) = {min_step*1000} [mV]")
        self.lg.debug(f"Largest step (max_step) = {max_step*1000} [mV]")
        self.lg.debug(f"NPLC = {self.sm.getNPLC()}")
        self.lg.debug(f"Snaith mode = {snaith_mode}")
        self.lg.debug(f"Source-measure delay = {delay_ms} [ms]")

        q = []  # main list of measurements
        process_q_len = 20
        m = deque(maxlen=process_q_len)  # measurement buffer for the mppt algorithm, newest values at the start
        # x = deque(maxlen=process_q_len)  # keeps independant variable setpoints

        if snaith_mode == True:
            duration = duration - snaith_pre_soak_t - snaith_post_soak_t
            this_soak_t = snaith_pre_soak_t
            self.lg.debug("Snaith Pre Soaking @ Mpp (V={:0.2f}[mV]) for {:0.1f} seconds...".format(start_voltage * 1000, this_soak_t))
            spos = self.sm.measureUntil(t_dwell=this_soak_t, cb=callback)
            q += spos

            if self.killer.is_set():
                self.lg.debug("Killed by killer.")
                return q

        # the objective function we'll be trying to find the minimum of here is power (density) produced by the sourcemeter
        objective = lambda var: var[0] * var[1] / self.area

        # function to get the sign of a number
        sign = lambda num: (1, -1)[int(num < 0)]

        # register a bootstrap measurement
        w = start_voltage
        m.appendleft(self.measure(w, q, delay_ms=delay_ms, callback=callback))
        # x.appendleft(w)
        run_time = time.time() - self.t0

        # we don't know too much about which way is down the gradient before we actually get started running the mppt algo here,
        # so let's seed with this initial delta value
        delta = delta_zero
        w += delta

        def compute_grad(input, scale_time=True):
            obj0 = objective(input[0])  # this objective
            obj1 = objective(input[1])  # last objective
            v0 = input[0][0]  # this voltage
            v1 = input[1][0]  # last voltage

            # gradient in voltage
            if v0 == v1:
                grad = None  # don't try to divide by zero
            else:
                grad = (obj0 - obj1) / (v0 - v1)

            # gradient in time and voltage
            if scale_time and (grad is not None):
                time0 = input[0][2]  # this timestamp
                time1 = input[1][2]  # last timestamp
                if time0 == time1:
                    grad = None  # don't try to divide by zero
                else:
                    grad = grad / (time0 - time1)
            return grad

        # the mppt loop
        i = 0
        jump_sign = 1
        last_jump_time = 0
        while (not self.killer.is_set()) and (run_time < duration):  # exit via killer or timeout
            i += 1
            some_sign = random.choice([-1, 1])
            m.appendleft(self.measure(w, q, delay_ms=delay_ms, callback=callback))  # apply new voltage and record a measurement and store the result in slot 0
            # x.appendleft(w)  # record independant variable

            # compute a gradient value
            gradient = compute_grad(m, scale_time=time_scale)

            if gradient is not None:
                # use gradient descent with momentum algo to compute our next voltage step
                delta = -1 * alpha * gradient + momentum * delta
            else:  # handle divide by zero case
                if min_step == 0:
                    delta = some_sign * 0.0001
                else:
                    delta = some_sign * min_step

            # enforce step size limits
            if (abs(delta) < min_step) and (min_step > 0):  # enforce minimum step size if we're doing that
                delta = some_sign * min_step
            elif (abs(delta) > max_step) and (max_step < float("inf")):  # enforce maximum step size if we're doing that
                delta = sign(delta) * max_step

            # update runtime
            run_time = time.time() - self.t0

            if (run_time > jump_period) and (jump_period > 0) and (jump_percent != 0) and ((run_time - last_jump_time) > jump_period):
                # force a perturbation if that's enabled
                delta = jump_sign * self.Voc * jump_percent / 100
                jump_sign = jump_sign * -1
                last_jump_time = run_time

            # apply voltage step, calculate new voltage
            w += delta

        if snaith_mode == True:
            this_soak_t = snaith_post_soak_t
            self.lg.debug("Snaith Post Soaking @ Mpp (V={:0.2f}[mV]) for {:0.1f} seconds...".format(start_voltage * 1000, this_soak_t))
            spos = self.sm.measureUntil(t_dwell=this_soak_t, cb=callback)
            q += spos

        # take whatever the most recent readings were to be the mppt
        self.Vmpp = m[0][0]
        self.Impp = m[0][1]

        return q

    def measure(self, setpoint: float, q: list, delay_ms: float = 0, callback: typing.Callable[[list[tuple[float, float, float, int]]], None] = lambda x: None):
        """changes the smu's setpoint and makes a measurement"""
        # enforce quadrant restrictions to prevent the mppt from erroniously wandering out of the power quadrant
        if (self.quadrant_lock) and (setpoint < 0):
            setpoint = 0.0001
        elif (not self.quadrant_lock) and (setpoint > 0):
            setpoint = -0.0001

        self.sm.setSource(setpoint)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000)
        measurement = self.sm.measure()
        callback(measurement)

        v, i, tx, status = measurement[0]

        q += measurement
        return (v, i, tx)

    def really_dumb_tracker(self, duration, start_voltage, callback: typing.Callable[[list[tuple[float, float, float, int]]], None] = lambda x: None, dAngleMax: float = 7.0, dwell_time: float = 10.0, sweep_delay_ms: float = 30.0):
        """
        A super dumb maximum power point tracking algorithm that
        alternates between periods of exploration around the mppt and periods of constant voltage dwells
        runs for duration seconds and returns a nx4 deque of the measurements it made
        dAngleMax, exploration limits, [exploration degrees] (plus and minus)
        dwell_time, dwell period duration in seconds
        """

        # we must know these to continue
        assert self.t0 is not None
        assert self.Voc is not None

        self.lg.debug("===Starting up dumb maximum power point tracking algorithm===")
        self.lg.debug(f"dAngleMax = {dAngleMax} [deg]")
        self.lg.debug(f"dwell_time = {dwell_time} [s]")
        self.lg.debug(f"sweep_delay_ms = {sweep_delay_ms} [ms]")

        # work in voltage steps that are this fraction of Voc
        dV = self.Voc / 301

        q = []
        Vmpp = start_voltage

        if duration <= 10:
            # if the user only wants to mppt for 20 or less seconds, shorten the initial dwell
            initial_soak = duration * 0.2
        else:
            initial_soak = dwell_time

        self.lg.debug("Soaking @ Mpp (V={:0.2f}[mV]) for {:0.1f} seconds...".format(Vmpp * 1000, initial_soak))
        ssmpps = self.sm.measureUntil(t_dwell=initial_soak, cb=callback)
        self.Impp = ssmpps[-1][1]  # use most recent current measurement as Impp
        if self.Isc is None:
            # if nobody told us otherwise, just assume Isc is 10% higher than Impp
            self.Isc = self.Impp * 1.1
        q += ssmpps
        if self.killer.is_set():
            self.lg.debug("Killed by killer.")
            return q

        Impp = self.Impp
        Voc = self.Voc
        Isc = self.Isc

        run_time = time.time() - self.t0
        while (not self.killer.is_set()) and (run_time < duration):
            self.lg.debug("Exploring for new Mpp...")
            i_explore = numpy.array(Impp)
            v_explore = numpy.array(Vmpp)

            angleMpp = numpy.rad2deg(numpy.arctan(Impp / Vmpp * Voc / Isc))
            self.lg.debug("MPP ANGLE = {:0.2f}".format(angleMpp))
            v_set = Vmpp
            highEdgeTouched = False
            lowEdgeTouched = False
            while (not self.killer.is_set()) and not (highEdgeTouched and lowEdgeTouched):
                (v, i, t) = self.measure(v_set, q, delay_ms=sweep_delay_ms, callback=callback)
                run_time = t - self.t0

                i_explore = numpy.append(i_explore, i)
                v_explore = numpy.append(v_explore, v)
                thisAngle = numpy.rad2deg(numpy.arctan(i / v * Voc / Isc))
                dAngle = angleMpp - thisAngle
                # self.lg.debug("dAngle={:}, highEdgeTouched={:}, lowEdgeTouched={:}".format(dAngle, highEdgeTouched, lowEdgeTouched))

                if (highEdgeTouched == False) and (dAngle > dAngleMax):
                    highEdgeTouched = True
                    dV = dV * -1
                    self.lg.debug("Reached high voltage edge because angle exceeded")

                if (lowEdgeTouched == False) and (dAngle < -dAngleMax):
                    lowEdgeTouched = True
                    dV = dV * -1
                    self.lg.debug("Reached low voltage edge because angle exceeded")

                v_set = v_set + dV
                if ((v_set > 0) and (dV > 0)) or ((v_set < 0) and (dV < 0)):  #  walking towards Voc
                    if (highEdgeTouched == False) and (dV > 0) and v_set >= Voc:
                        highEdgeTouched = True
                        dV = dV * -1  # switch our voltage walking direction
                        v_set = v_set + dV
                        self.lg.debug("WARNING: Reached high voltage edge because we hit Voc")

                    if (lowEdgeTouched == False) and (dV < 0) and v_set <= Voc:
                        lowEdgeTouched = True
                        dV = dV * -1  # switch our voltage walking direction
                        v_set = v_set + dV
                        self.lg.debug("WARNING: Reached high voltage edge because we hit Voc")

                else:  #  walking towards Jsc
                    if (highEdgeTouched == False) and (dV > 0) and v_set >= 0:
                        highEdgeTouched = True
                        dV = dV * -1  # switch our voltage walking direction
                        v_set = v_set + dV
                        self.lg.debug("WARNING: Reached low voltage edge because we hit 0V")

                    if (lowEdgeTouched == False) and (dV < 0) and v_set <= 0:
                        lowEdgeTouched = True
                        dV = dV * -1  # switch our voltage walking direction
                        v_set = v_set + dV
                        self.lg.debug("WARNING: Reached low voltage edge because we hit 0V")

            self.lg.debug("Done exploring.")

            # find the powers for the values we just explored
            p_explore = v_explore * i_explore * -1
            maxIndex = numpy.argmax(p_explore)
            Vmpp = v_explore[maxIndex]
            Impp = i_explore[maxIndex]

            self.lg.debug("New Mpp found: {:.6f} mW @ {:.6f} V".format(p_explore[maxIndex] * 1000, Vmpp))

            dFromLastMppAngle = angleMpp - numpy.rad2deg(numpy.arctan(Impp / Vmpp * Voc / Isc))

            self.lg.debug("That's {:.6f} degrees different from the previous Mpp.".format(dFromLastMppAngle))

            # time_left = duration - run_time

            # if time_left <= 0:
            #  break

            self.lg.debug("Teleporting to Mpp!")
            self.sm.setSource(Vmpp)

            # if time_left < dwell_time:
            #  dwell = time_left
            # else:
            dwell = dwell_time

            self.lg.debug("Dwelling @ Mpp (V={:0.2f}[mV]) for {:0.1f} seconds...".format(Vmpp * 1000, dwell))
            dq = self.sm.measureUntil(t_dwell=dwell, cb=callback)
            Impp = dq[-1][1]
            q += dq

            run_time = time.time() - self.t0

        if self.killer.is_set():
            self.lg.debug("Killed by killer.")

        self.Impp = Impp
        self.Vmpp = Vmpp
        return q
