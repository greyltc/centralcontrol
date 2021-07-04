"""MPPT."""

import numpy
import time
import random
import warnings
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
        self.absolute_current_limit = abs(absolute_current_limit)

    def reset(self):
        """Reset params."""
        self.Voc = {}
        self.Isc = {}
        self.Mmpp = {}  # measurement from max power point
        self.Vmpp = {}  # voltage at max power point
        self.Impp = {}  # current at max power point
        self.Pmax = {}  # power at max power point
        self.abort = False

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

                if ch in self.Pmax:
                    if Pmax > self.Pmax[ch]:
                        old_Pmax_exceeded = True
                    else:
                        old_Pmax_exceeded = False
                else:
                    old_Pmax_exceeded = False

                if (self.Pmax is {}) or (old_Pmax_exceeded == True):
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
        voc_compliance=3,
        i_limit=0.1,
        extra="basic://7:10:10",
        pixels={},
    ):
        """Luanch mppt.

        general function to call begin a max power point tracking algorithm
        duration given in seconds, optionally calling callback function on each
        measurement point
        """
        m = []  # list holding mppt measurements
        self.t0 = time.time()  # start the mppt timer

        if abs(i_limit) > abs(self.absolute_current_limit):
            i_limit = abs(self.absolute_current_limit)

        if NPLC != -1:
            self.sm.nplc = NPLC

        channels = [ch for ch in pixels.keys()]

        if self.Voc == {}:
            # disable output for high impedance mode Voc measurement
            self.sm.enable_output(False, channels)
            ssvocs = self.sm.measure(channels, measurement="dc")
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
        values = {}
        for ch, vmp in sorted(self.Vmpp.items()):
            values[ch] = vmp
        self.sm.configure_dc(values, "v")
        self.sm.enable_output(True, channels)

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
        #     if len(params) == 0:  #  use defaults
        #         m.append(
        #             m_tracked := self.really_dumb_tracker(duration, callback=callback)
        #         )
        #     else:
        #         params = params.split(":")
        #         if len(params) != 3:
        #             raise (
        #                 ValueError(
        #                     "MPPT configuration failure, Usage: --mppt-params basic://[degrees]:[dwell]:[sweep_delay_ms]"
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
                    self.gradient_descent(
                        duration,
                        start_voltage=self.Vmpp,
                        alpha=0.05,
                        min_step=0.001,
                        NPLC=10,
                        callback=callback,
                        delay_ms=500,
                        snaith_mode=do_snaith,
                        max_step=0.1,
                        momentum=0.1,
                        delta_zero=0.01,
                        pixels=pixels,
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
                    self.gradient_descent(
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
                        pixels=pixels,
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
        alpha=0.05,
        min_step=0.001,
        NPLC=10,
        snaith_mode=False,
        delay_ms=500,
        max_step=0.1,
        momentum=0.1,
        delta_zero=0.01,
        pixels={},
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

            # init container for ss data
            spos = {}
            for ch, _ in pixels.items():
                spos[ch] = []

            # run steady state measurement
            t0 = time.time()
            while time.time() - t0 < this_soak_t:
                time.sleep(delay_ms / 1000)
                data = self.sm.measure([ch for ch in pixels.keys()], measurement="dc")
                self.detect_short_circuits(data, pixels)
                tuple_data = self.tuplify_data(data)
                callback(tuple_data)
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
        data = self.sm.measure([ch for ch in pixels.keys()], measurement="dc")
        self.detect_short_circuits(data, pixels)
        tuple_data = self.tuplify_data(data)
        callback(tuple_data)
        m.appendleft(data)
        # x.appendleft(w)
        run_time = time.time() - self.t0

        # we don't know too much about which way is down the gradient before we
        # actually get started running the mppt algo here, so let's seed with this
        # initial delta value
        deltas = {}
        for ch in pixels.keys():
            deltas[ch] = delta_zero

        next_voltages = {}
        for ch in pixels.keys():
            new_v = self.Vmpp[ch] + deltas[ch]
            if (self.voltage_lock is True) and (new_v < 0):
                new_v = 0.0001
            elif (self.voltage_lock is False) and (new_v > 0):
                new_v = -0.0001
            next_voltages[ch] = new_v

        def compute_grad(data):
            # this measurement
            obj0s = {}
            v0s = {}
            t0s = {}
            for ch, ch_data in sorted(data[0].items()):
                obj0s[ch] = objective(ch_data[0])
                v0s[ch] = ch_data[0][0]
                t0s[ch] = ch_data[0][2]

            # last measurement
            obj1s = {}
            v1s = {}
            t1s = {}
            for ch, ch_data in sorted(data[1].items()):
                obj1s[ch] = objective(ch_data[0])
                v1s[ch] = ch_data[0][0]
                t1s[ch] = ch_data[0][2]

            gradient = {}
            for ch in data[0].keys():
                if v0s[ch] == v1s[ch]:
                    # don't try to divide by zero
                    gradient[ch] = None
                else:
                    # find the gradient
                    gradient[ch] = (
                        (obj0s[ch] - obj1s[ch])
                        / (v0s[ch] - v1s[ch])
                        / (t0s[ch] - t1s[ch])
                    )

            return gradient

        # the mppt loop
        i = 0
        while not self.abort and (run_time < duration):
            i += 1
            some_sign = random.choice([-1, 1])

            # apply new voltage and record a measurement and store the result in slot 0
            self.sm.configure_dc(next_voltages, "v")
            time.sleep(delay_ms / 1000)
            data = self.sm.measure([ch for ch in pixels.keys()], measurement="dc")
            self.detect_short_circuits(data, pixels)
            m.appendleft(data)
            tuple_data = self.tuplify_data(data)
            callback(tuple_data)
            # record independant variable
            # x.appendleft(w)

            # compute a gradient value
            gradients = compute_grad(m)
            for ch, gradient in gradients.items():
                if gradient is not None:
                    # use gradient descent with momentum algo to compute our next
                    # voltage step
                    deltas[ch] = -1 * alpha * gradient + momentum * deltas[ch]
                else:
                    # handle divide by zero case
                    if min_step == 0:
                        deltas[ch] = some_sign * 0.0001
                    else:
                        deltas[ch] = some_sign * min_step

            # enforce step size limits
            for ch, delta in deltas.items():
                if (abs(delta) < min_step) and (min_step > 0):
                    # enforce minimum step size if we're doing that
                    deltas[ch] = some_sign * min_step
                elif (abs(delta) > max_step) and (max_step < float("inf")):
                    # enforce maximum step size if we're doing that
                    deltas[ch] = sign(delta) * max_step

            # apply voltage step, calculate new voltage
            for ch in pixels.keys():
                new_v = next_voltages[ch] + deltas[ch]
                if (self.voltage_lock is True) and (new_v < 0):
                    new_v = 0.0001
                elif (self.voltage_lock is False) and (new_v > 0):
                    new_v = -0.0001
                next_voltages[ch] = new_v

            # update runtime
            run_time = time.time() - self.t0

        if snaith_mode is True:
            this_soak_t = snaith_post_soak_t

            print(
                f"Snaith Pre Soaking @ Mpp (V={start_voltage:0.2f} [V]) for "
                + f"{this_soak_t:0.1f} seconds..."
            )

            spos = {}
            for ch in pixels.keys():
                spos[ch] = []

            # run steady state measurement
            t0 = time.time()
            while time.time() - t0 < this_soak_t:
                data = self.sm.measure([ch for ch in pixels.keys()], "dc")
                self.detect_short_circuits(data, pixels)
                tuple_data = self.tuplify_data(data)
                callback(tuple_data)
                for ch, ch_data in sorted(data.items()):
                    spos[ch].extend(ch_data)
                time.sleep(delay_ms / 1000)

            self.q.extend(spos)

        # take whatever the most recent readings were to be the mppt
        for ch, ch_data in m[0].items():
            self.Vmpp[ch] = ch_data[0][0]
            self.Impp[ch] = ch_data[0][1]

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

    def detect_short_circuits(self, data, pixels):
        """Check status code of SMU measurements and disable a channel if it's shorted.

        Disabling a channel means removing it from the pixels dictionary. Also discard
        shorted data.

        Paramters
        ---------
        data : dict
            SMU measurement data dictionary. Keys are SMU channel numbers.
        pixels : dict
            Pixel information dictionary. Keys are SMU channel numbers.
        """
        channels = [ch for ch in pixels.keys()]

        for ch in channels:
            ch_data = data[ch]
            statuses = [row[3] for row in ch_data]
            if 1 in statuses:
                # current has exceeded smu i_threshold so disable channel and stop
                # measuring it
                self.sm.enable_output(False, ch)
                pixels.pop(ch, None)
                warnings.warn(
                    f"Short circuit detected on channel {ch}! The device connected to "
                    + "this channel will no longer be measured."
                )

                # remove shorted data
                data.pop(ch, None)
            elif 2 in statuses:
                # smu overcurrent on its input has occured so stop measuring channel
                # this takes out both channels on smu board so need to figure out which
                # is shorted
                # first check if it's already been removed from list by a previous
                # detection on the other board channel
                if ch in pixels.keys():
                    # get other channel number on board
                    if ch % 2 == 0:
                        other_ch = ch + 1
                    else:
                        other_ch = ch - 1

                    # disable ch and measure other_ch
                    self.sm.enable_output(False, ch)
                    status = self.sm.measure(other_ch, "dc")[ch][0][3]
                    if status == 2:
                        # other channel on board is shorted, re-enable ch
                        self.sm.enable_output(True, ch)

                        # disable and remove other_ch
                        self.sm.enable_output(False, other_ch)
                        pixels.pop(other_ch, None)
                        warnings.warn(
                            f"Short circuit detected on channel {other_ch}! The device"
                            + " connected to this channel will no longer be measured."
                        )
                        # remove shorted data
                        data.pop(other_ch, None)

                        # ch could still also be shorted so check it
                        status = self.sm.measure(ch, "dc")[ch][0][3]
                        if status == 2:
                            # it is shorted so disable and remove it
                            self.sm.enable_output(False, ch)
                            pixels.pop(ch, None)
                            warnings.warn(
                                f"Short circuit detected on channel {ch}! The device"
                                + " connected to this channel will no longer be "
                                + "measured."
                            )

                            # remove shorted data
                            data.pop(ch, None)
                    else:
                        # ch is shorted so remove it
                        pixels.pop(ch, None)
                        warnings.warn(
                            f"Short circuit detected on channel {ch}! The device"
                            + " connected to this channel will no longer be "
                            + "measured."
                        )

                        # remove shorted data
                        data.pop(ch, None)
            else:
                pass

    def tuplify_data(self, data):
        """Convert lists in dictionary data to tuples.

        Parameters
        ----------
        data : dictionary
            Dictionary of data returned from SMU. Keys are channel numbers and values are
            lists of tuples.

        Returns
        -------
        tuple_data : dictionary
            Dictionary of data where keys are channel numbers and values are tuples.
        """
        # only send first element of data list to handler for single-shot
        # measurements
        tuple_data = {}
        for ch, ch_data in data.items():
            tuple_data[ch] = ch_data[0]

        return tuple_data

