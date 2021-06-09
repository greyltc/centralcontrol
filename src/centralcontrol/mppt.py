"""MPPT."""

import numpy
import time
import random
from collections import deque


class mppt:
    """Maximum power point tracker class."""

    Voc = {}
    Isc = {}
    Mmpp = {}  # measurement from max power point
    Vmpp = {}  # voltage at max power point
    Impp = {}  # current at max power point
    Pmax = {}  # power at max power point (for keeping track of voc and isc)
    abort = False

    # under no circumstances should we violate this
    absolute_current_limit = 0.1  # always safe default

    currentCompliance = None
    t0 = None  # the time we started the mppt algorithm

    def __init__(self, sm, absolute_current_limit):
        """Construct object."""
        self.sm = sm
        self.absolute_current_limit = absolute_current_limit

    def reset(self):
        """Reset params."""
        self.Voc = {}
        self.Isc = {}
        self.Mmpp = {}  # measurement from max power point
        self.Vmpp = {}  # voltage at max power point
        self.Impp = {}  # current at max power point
        self.Pmax = {}  # power at max power point
        self.abort = False

        self.current_compliance = None
        self.t0 = None  # the time we started the mppt algorithm

    def register_curve(self, vector, light=True):
        """Register an IV curve with the max power point tracker.

        Given a dictionary or lists of raw measurements for each smu channel, figures
        out which one produced the highest power

        Updates some values for mppt if light=True
        """
        Vmpps = {}
        Pmaxs = {}
        Impps = {}
        maxIndexs = {}
        for ch, ch_data in sorted(vector.items()):
            v = numpy.array([e[0] for e in ch_data])
            i = numpy.array([e[1] for e in ch_data])
            t = numpy.array([e[2] for e in ch_data])
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
            Vmpps[ch] = Vmpp
            Pmaxs[ch] = Pmax
            Impps[ch] = Impp
            maxIndexs[ch] = maxIndex
            if light is True:  # this was from a light i-v curve
                print(
                    "MPPT IV curve inspector investigating new light curve params: "
                    + f"{(Pmax, Vmpp, Impp, Voc, Isc)}"
                )
                if (self.Pmax is {}) or (Pmax > self.Pmax[ch]):
                    if self.Pmax is {}:
                        because = "there was no previous one."
                    else:
                        because = (
                            f"we beat the old max power value, {Pmax} > "
                            + f"{self.Pmax[ch]} [W]"
                        )
                    print(
                        f"New refrence IV curve found for MPPT algo because {because}"
                    )
                    self.Vmpp[ch] = Vmpp
                    self.Impp[ch] = Impp
                    self.Pmax[ch] = Pmax
                    # store off measurement value for this one
                    self.Mmpp[ch] = (Vmpp, Impp, Tmpp)
                    print(
                        f"V_mpp = {self.Vmpp[ch]}[V]\nI_mpp = {self.Impp[ch]}[A]\n"
                        + f"P_max = {self.Pmax[ch]}[W]"
                    )
                    if (min(v) <= 0) and (max(v) >= 0):
                        # if we had data on both sizes of 0V, then we can estimate Isc
                        self.Isc[ch] = Isc
                        print(f"I_sc = {self.Isc[ch]}[A]")
                    if (min(i) <= 0) and (max(i) >= 0):
                        # if we had data on both sizes of 0A, then we can estimate Voc
                        self.Voc[ch] = Voc
                        print(f"V_oc = {self.Voc[ch]}[V]")

        # returns dict of maximum power[W], Vmpp, Impp and the index
        return (Pmaxs, Vmpps, Impps, maxIndexs)

    def launch_tracker(
        self,
        duration=30,
        callback=lambda x: None,
        NPLC=-1,
        i_limit=0.04,
        extra="basic://7:10",
    ):
        """Luanch mppt.

        general function to call begin a max power point tracking algorithm
        duration given in seconds, optionally calling callback function on each
        measurement point
        """
        m = []  # list holding mppt measurements
        self.t0 = time.time()  # start the mppt timer

        if (self.current_compliance is None) or (i_limit < self.current_compliance):
            self.current_compliance = i_limit

        if NPLC != -1:
            self.sm.nplc = NPLC

        if self.Voc == {}:
            self.sm.configure_dc(0, "i")
            ssvocs = self.sm.measure()
            for ch, ch_data in sorted(ssvocs.items()):
                self.Voc[ch] = ch_data[-1][0]
            print(
                f"mppt algo had to find V_oc = {self.Voc} [V] because nobody gave us "
                + "any voltage info..."
            )
        else:
            ssvocs = {}

        if self.Vmpp == {}:
            # start at 70% of Voc if nobody told us otherwise
            for ch, voc in sorted(self.Voc.items()):
                self.Vmpp[ch] = 0.7 * voc
            print(
                f"mppt algo assuming V_mpp = {self.Vmpp} [V] from V_oc because nobody "
                + "told us otherwise..."
            )

        # get the smu ready for doing the mppt
        values = []
        for ch, vmp in sorted(self.Vmpp.items()):
            values.append(vmp)
        self.sm.configure_dc(values, "v")

        # this locks the smu to the device's power quadrant
        # all devices have to be in the same quadrant so just check first one
        if self.Voc[0] >= 0:
            self.voltage_lock = True  # lock mppt voltage to be >0
        else:
            self.voltage_lock = False  # lock mppt voltage to be <0

        # run a tracking algorithm
        extra_split = extra.split(sep="://", maxsplit=1)
        algo = extra_split[0]
        params = extra_split[1]
        # if algo == "basic":
        #     if len(params) == 0:
        #         # use defaults
        #         m.append(
        #             m_tracked := self.really_dumb_tracker(duration, callback=callback)
        #         )
        #     else:
        #         params = params.split(":")
        #         if len(params) != 3:
        #             raise (
        #                 ValueError(
        #                     "MPPT configuration failure, Usage: --mppt-params "
        #                     + "basic://[degrees]:[dwell]:[sweep_delay_ms]"
        #                 )
        #             )
        #         params = [float(f) for f in params]
        #         m.append(
        #             m_tracked := self.really_dumb_tracker(
        #                 duration,
        #                 callback=callback,
        #                 dAngleMax=params[0],
        #                 dwell_time=params[1],
        #                 sweep_delay_ms=params[2],
        #             )
        #         )
        if algo in ["gd", "snaith"]:
            if algo == "snaith":
                do_snaith = True
            else:
                do_snaith = False
            if len(params) == 0:
                # use defaults
                m.append(
                    m_tracked := self.gradient_descent(
                        duration,
                        start_voltage=self.Vmpp,
                        alpha=0.8,
                        min_step=0.001,
                        NPLC=-1,
                        callback=callback,
                        delay_ms=1000,
                        snaith_mode=do_snaith,
                        max_step=0.1,
                        momentum=0,
                        delta_zero=0,
                    )
                )
            else:
                params = params.split(":")
                if len(params) != 7:
                    raise (
                        ValueError(
                            "MPPT configuration failure, Usage: --mppt-params gd://"
                            + "[alpha]:[min_step]:[NPLC]:[delayms]:[max_step]:"
                            + "[momentum]:[delta_zero]"
                        )
                    )
                params = [float(f) for f in params]
                m.append(
                    m_tracked := self.gradient_descent(
                        duration,
                        start_voltage=self.Vmpp,
                        callback=callback,
                        alpha=params[0],
                        min_step=params[1],
                        NPLC=params[2],
                        delay_ms=params[3],
                        snaith_mode=do_snaith,
                        max_step=params[4],
                        momentum=params[5],
                        delta_zero=params[6],
                    )
                )
        else:
            print(
                f"WARNING: MPPT algorithm {algo} not understood, not doing max power "
                + f"point tracking"
            )

        return (m, ssvocs)

    def gradient_descent(
        self,
        duration,
        start_voltage,
        callback=lambda x: None,
        alpha=0.8,
        min_step=0.001,
        NPLC=-1,
        snaith_mode=False,
        delay_ms=1000,
        max_step=0.1,
        momentum=0,
        delta_zero=0.001,
    ):
        """Run gradient descent MPPT algorithm.

        alpha is the "learning rate"
        min_step is the minimum voltage step size the algorithm will be allowed to take
        delay is the number of ms to wait between setting the voltage and making a
        measurement
        """
        # snaith mode constants
        snaith_pre_soak_t = 15
        snaith_post_soak_t = 3

        if NPLC != -1:
            self.sm.nplc = NPLC

        if delay_ms != -1:
            self.sm.settling_delay = delay_ms / 1000

        print(
            "===Starting up gradient descent maximum power point tracking algorithm==="
        )
        print(f"Learning rate (alpha) = {alpha}")
        print(f"V_initial = {start_voltage} [V]")
        print(f"delta_zero = {delta_zero} [V]")  # first step
        print(f"momentum = {momentum}")
        print(f"Smallest step (min_step) = {min_step*1000} [mV]")
        print(f"Largest step (max_step) = {max_step*1000} [mV]")
        print(f"NPLC = {self.sm.nplc}")
        print(f"Snaith mode = {snaith_mode}")
        print(f"Source-measure delay = {delay_ms} [ms]")

        self.q = deque()
        process_q_len = 20
        # measurement buffer for the mppt algorithm
        m = deque(maxlen=process_q_len)
        # x = deque(maxlen=process_q_len)  # keeps independant variable setpoints

        if snaith_mode is True:
            duration = duration - snaith_pre_soak_t - snaith_post_soak_t
            this_soak_t = snaith_pre_soak_t
            print(
                f"Snaith Pre Soaking @ Mpp (V={start_voltage:0.2f} [V]) for "
                + f"{this_soak_t:0.1f} seconds..."
            )

            spos = {}
            for ch in range(self.sm.num_channels):
                spos[ch] = []

            # run steady state measurement
            t0 = time.time()
            while time.time() - t0 < this_soak_t:
                data = self.sm.measure()
                callback(data)
                for ch, ch_data in sorted(data.items()):
                    spos[ch].extend(ch_data)

            self.q.extend(spos)

        # the objective function we'll be trying to find the minimum of here is power
        # produced by the sourcemeter
        def objective(var):
            return var[0] * var[1]

        def sign(num):
            """Get the sign of a number."""
            return (1, -1)[int(num < 0)]

        # register a bootstrap measurement
        m.appendleft(self.sm.measure())
        # x.appendleft(w)
        run_time = time.time() - self.t0

        # we don't know too much about which way is down the gradient before we
        # actually get started running the mppt algo here, so let's seed with this
        # initial delta value
        deltas = [delta_zero] * len(self.Vmpp)
        next_voltages = []
        for ch, vmp in sorted(self.Vmpp.items()):
            next_voltages.append(vmp + deltas[ch])

        def compute_grad(data):
            # this measurement
            obj0s = []
            v0s = []
            for ch, ch_data in sorted(data[0].items()):
                obj0s.append(objective(ch_data))
                v0s.append(ch_data[0])

            # last measurement
            obj1s = []
            v1s = []
            for ch, ch_data in sorted(data[1].items()):
                obj1s.append(objective(ch_data))
                v1s.append(ch_data[0])

            gradient = []
            for obj0, obj1, v0, v1 in zip(obj0s, obj1s, v0s, v1s):
                if v0 == v1:
                    # don't try to divide by zero
                    gradient.append(None)
                else:
                    # find the gradient
                    gradient.append((obj0 - obj1) / (v0 - v1))

            return gradient

        # the mppt loop
        i = 0
        while not self.abort and (run_time < duration):
            i += 1
            some_sign = random.choice([-1, 1])

            # apply new voltage and record a measurement and store the result in slot 0
            self.sm.configure_dc(next_voltages, "v")
            m.appendleft(self.sm.measure())
            # record independant variable
            # x.appendleft(w)

            # compute a gradient value
            gradients = compute_grad(m)
            for ix, gradient in enumerate(gradients):
                if gradient is not None:
                    # use gradient descent with momentum algo to compute our next
                    # voltage step
                    deltas[ix] = -1 * alpha * gradient + momentum * deltas[ix]
                else:
                    # handle divide by zero case
                    if min_step == 0:
                        deltas[ix] = some_sign * 0.0001
                    else:
                        deltas[ix] = some_sign * min_step

            # enforce step size limits
            for ix, delta in enumerate(deltas):
                if (abs(delta) < min_step) and (min_step > 0):
                    # enforce minimum step size if we're doing that
                    deltas[ix] = some_sign * min_step
                elif (abs(delta) > max_step) and (max_step < float("inf")):
                    # enforce maximum step size if we're doing that
                    deltas[ix] = sign(delta) * max_step

            # apply voltage step, calculate new voltage
            for ix, (v, delta) in enumerate(zip(next_voltages, deltas)):
                next_voltages[ix] = v + delta

            # update runtime
            run_time = time.time() - self.t0

        if snaith_mode is True:
            this_soak_t = snaith_post_soak_t

            print(
                f"Snaith Pre Soaking @ Mpp (V={start_voltage:0.2f} [V]) for "
                + f"{this_soak_t:0.1f} seconds..."
            )

            spos = {}
            for ch in range(self.sm.num_channels):
                spos[ch] = []

            # run steady state measurement
            t0 = time.time()
            while time.time() - t0 < this_soak_t:
                data = self.sm.measure()
                callback(data)
                for ch, ch_data in sorted(data.items()):
                    spos[ch].extend(ch_data)

            self.q.extend(spos)

        # take whatever the most recent readings were to be the mppt
        for ch, ch_data in m[0]:
            self.Vmpp[ch] = ch_data[0]
            self.Impp[ch] = ch_data[0]

        q = self.q
        del self.q
        return q

    # def measure(self, v_set, delay_ms=0, callback=lambda x: None):
    #     """Set the voltage and make a measurement."""
    #     # enforce quadrant restrictions to prevent the mppt from erroniously wandering
    #     # out of the power quadrant
    #     if (self.voltage_lock is True) and (v_set < 0):
    #         v_set = 0.0001
    #     elif (self.voltage_lock is False) and (v_set > 0):
    #         v_set = -0.0001

    #     self.sm.setSource(v_set)
    #     time.sleep(delay_ms / 1000)
    #     measurement = self.sm.measure()[0]
    #     callback(measurement)

    #     v, i, tx, status = measurement

    #     self.q.append(measurement)
    #     return (v, i, tx)

    # def really_dumb_tracker(
    #     self,
    #     duration,
    #     callback=lambda x: None,
    #     dAngleMax=7,
    #     dwell_time=10,
    #     sweep_delay_ms=30,
    # ):
    #     """Dumb mppt.

    #     A super dumb maximum power point tracking algorithm that alternates between
    #     periods of exploration around the mppt and periods of constant voltage dwells.

    #     runs for duration seconds and returns a nx4 deque of the measurements it made.

    #     dAngleMax, exploration limits, [exploration degrees] (plus and minus)
    #     dwell_time, dwell period duration in seconds
    #     """
    #     print("===Starting up dumb maximum power point tracking algorithm===")
    #     print(f"dAngleMax = {dAngleMax} [deg]")
    #     print(f"dwell_time = {dwell_time} [s]")
    #     print(f"sweep_delay_ms = {sweep_delay_ms} [ms]")

    #     # work in voltage steps that are this fraction of Voc
    #     dV = max([voc for ch, voc in self.Voc]) / 301

    #     self.q = deque()
    #     Vmpp = self.Vmpp

    #     if duration <= 10:
    #         # if the user only wants to mppt for 10 or less seconds, shorten the
    #         # initial dwell
    #         initial_soak = duration * 0.2
    #     else:
    #         initial_soak = dwell_time

    #     print(f"Soaking @ Mpp (V={self.Vmpp} [V]) for {initial_soak:0.1f} seconds...")
    #     # init container for all data
    #     ssmpps = {}
    #     for ch in range(self.sm.num_channels):
    #         ssmpps[ch] = []

    #     # run steady state measurement
    #     t0 = time.time()
    #     while time.time() - t0 < initial_soak:
    #         data = self.sm.measure()
    #         callback(data)
    #         for ch, ch_data in sorted(data.items()):
    #             ssmpps[ch].extend(ch_data)

    #     # use most recent current measurement as Impp
    #     self.Impp = {}
    #     for ch, ch_data in sorted(ssmpps.items()):
    #         self.Impp[ch] = ch_data[-1][1]

    #     # if nobody told us otherwise, just assume Isc is 10% higher than Impp
    #     if self.Isc is {}:
    #         for ch, impp in sorted(self.Impp.items()):
    #             self.Isc[ch] = impp * 1.1

    #     self.q.extend(ssmpps)

    #     Impp = self.Impp
    #     Voc = self.Voc
    #     Isc = self.Isc

    #     run_time = time.time() - self.t0
    #     while not self.abort and (run_time < duration):
    #         print("Exploring for new Mpp...")
    #         i_explore = numpy.array(Impp)
    #         v_explore = numpy.array(Vmpp)

    #         angleMpp = numpy.rad2deg(numpy.arctan(Impp / Vmpp * Voc / Isc))
    #         print(f"MPP ANGLE = {angleMpp:0.2f}")
    #         v_set = Vmpp
    #         highEdgeTouched = False
    #         lowEdgeTouched = False
    #         while not self.abort and not (highEdgeTouched and lowEdgeTouched):
    #             (v, i, t) = self.measure(
    #                 v_set, delay_ms=sweep_delay_ms, callback=callback
    #             )
    #             run_time = t - self.t0

    #             i_explore = numpy.append(i_explore, i)
    #             v_explore = numpy.append(v_explore, v)
    #             thisAngle = numpy.rad2deg(numpy.arctan(i / v * Voc / Isc))
    #             dAngle = angleMpp - thisAngle
    #             # print(
    #             #     f"dAngle={dAngle}, highEdgeTouched={highEdgeTouched}, "
    #             #     + f"lowEdgeTouched={lowEdgeTouched}"
    #             # )

    #             if (highEdgeTouched is False) and (dAngle > dAngleMax):
    #                 highEdgeTouched = True
    #                 dV = dV * -1
    #                 print("Reached high voltage edge because angle exceeded")

    #             if (lowEdgeTouched is False) and (dAngle < -dAngleMax):
    #                 lowEdgeTouched = True
    #                 dV = dV * -1
    #                 print("Reached low voltage edge because angle exceeded")

    #             v_set = v_set + dV
    #             if ((v_set > 0) and (dV > 0)) or ((v_set < 0) and (dV < 0)):
    #                 #  walking towards Voc
    #                 if (highEdgeTouched is False) and (dV > 0) and v_set >= Voc:
    #                     highEdgeTouched = True
    #                     dV = dV * -1  # switch our voltage walking direction
    #                     v_set = v_set + dV
    #                     print("WARNING: Reached high voltage edge because we hit Voc")

    #                 if (lowEdgeTouched is False) and (dV < 0) and v_set <= Voc:
    #                     lowEdgeTouched = True
    #                     dV = dV * -1  # switch our voltage walking direction
    #                     v_set = v_set + dV
    #                     print("WARNING: Reached high voltage edge because we hit Voc")

    #             else:
    #                 #  walking towards Jsc
    #                 if (highEdgeTouched is False) and (dV > 0) and v_set >= 0:
    #                     highEdgeTouched = True
    #                     dV = dV * -1  # switch our voltage walking direction
    #                     v_set = v_set + dV
    #                     print("WARNING: Reached low voltage edge because we hit 0V")

    #                 if (lowEdgeTouched is False) and (dV < 0) and v_set <= 0:
    #                     lowEdgeTouched = True
    #                     dV = dV * -1  # switch our voltage walking direction
    #                     v_set = v_set + dV
    #                     print("WARNING: Reached low voltage edge because we hit 0V")

    #         print("Done exploring.")

    #         # find the powers for the values we just explored
    #         p_explore = v_explore * i_explore * -1
    #         maxIndex = numpy.argmax(p_explore)
    #         Vmpp = v_explore[maxIndex]
    #         Impp = i_explore[maxIndex]

    #         print(f"New Mpp found: {p_explore[maxIndex] * 1000:.6f} mW @ {Vmpp:.6f} V")

    #         dFromLastMppAngle = angleMpp - numpy.rad2deg(
    #             numpy.arctan(Impp / Vmpp * Voc / Isc)
    #         )

    #         print(
    #             f"That's {dFromLastMppAngle:.6f} degrees different from the previous "
    #             + "Mpp."
    #         )

    #         # time_left = duration - run_time

    #         # if time_left <= 0:
    #         #  break

    #         print("Teleporting to Mpp!")
    #         self.sm.setSource(Vmpp)

    #         # if time_left < dwell_time:
    #         #  dwell = time_left
    #         # else:
    #         dwell = dwell_time

    #         print(
    #             f"Dwelling @ Mpp (V={Vmpp * 1000:0.2f}[mV]) for {dwell:0.1f} seconds..."
    #         )
    #         dq = self.sm.measureUntil(t_dwell=dwell, cb=callback)
    #         Impp = dq[-1][1]
    #         self.q.extend(dq)

    #         run_time = time.time() - self.t0

    #     q = self.q
    #     del self.q
    #     self.Impp = Impp
    #     self.Vmpp = Vmpp
    #     return q
