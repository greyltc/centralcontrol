"""High level experiment functions."""

import numpy as np
import scipy as sp
from scipy.integrate import simps
import unicodedata
import re
import os
import time
import tempfile
import inspect
from collections import deque
import warnings

import central_control.virt as virt
from central_control.k2400 import k2400
from central_control.controller import controller
from central_control.mppt import mppt
from central_control.illumination import illumination
import central_control  # for __version__

import sr830
import sp2150
import dp800
import virtual_sr830
import virtual_sp2150
import virtual_dp800
import eqe


class fabric:
    """Experiment control logic."""

    def __init__(self):
        """Get software revision."""
        self.software_revision = central_control.__version__
        print("Software revision: {:s}".format(self.software_revision))

    def compliance_current_guess(self, area=None):
        """Guess what the compliance current should be for i-v-t measurements.

        Parameters
        ----------
        area : float
            Device area in cm^2.
        """
        # set maximum current density (in mA/cm^2) slightly higher than an ideal Si
        # cell
        max_j = 50

        # calculate equivalent current in A for given device area
        # multiply by 5 to allow more data to be taken in forward bias (useful for
        # equivalent circuit fitting)
        # reduce to maximum compliance of keithley 2400 if too high
        if area is None:
            # no area info given so can't make a calcualted guess
            compliance_i = 0.1
        elif (compliance_i := 5 * max_j * area / 1000) > 1:
            compliance_i = 1

        return compliance_i

    def connect_smu(
        self,
        dummy=False,
        visa_lib="@py",
        smu_address=None,
        smu_terminator="\n",
        smu_baud=57600,
        smu_front_terminals=False,
        smu_two_wire=False,
    ):
        """Create smu connection.

        Parameters
        ----------
        dummy : bool
            Choose whether or not to make all instruments virtual. Useful for testing
            control logic.
        visa_lib : str
            PyVISA backend.
        smu_address : str
            VISA resource name for the source-measure unit. If `None` is given a
            virtual instrument is created.
        smu_terminator : str
            Termination character for communication with the source-measure unit.
        smu_baud : int
            Baud rate for serial communication with the source-measure unit.
        smu_front_terminals : bool
            Flag whether to use the front terminals of the source-measure unit.
        smu_two_wire : bool
            Flag whether to measure in two-wire mode. If `False` measure in four-wire mode.
        """
        if dummy is True:
            self.sm = virt.k2400()
        elif smu_address is None:
            return
        else:
            self.sm = k2400(
                visa_lib=visa_lib,
                terminator=smu_terminator,
                addressString=smu_address,
                serialBaud=smu_baud,
            )
        self.sm_idn = self.sm.idn

        # set up smu terminals
        self.sm.setTerminals(front=smu_front_terminals)
        self.sm.setWires(twoWire=smu_two_wire)

        # instantiate max-power tracker object based on smu
        self.mppt = mppt(self.sm)

    def connect_lia(
        self,
        dummy=False,
        visa_lib="@py",
        lia_address=None,
        lia_terminator="\r",
        lia_baud=9600,
        lia_output_interface=0,
    ):
        """Create lock-in amplifier connection.

        Parameters
        ----------
        dummy : bool
            Choose whether or not to make all instruments virtual. Useful for testing
            control logic.
        visa_lib : str
            PyVISA backend.
        lia_address : str
            VISA resource name for the lock-in amplifier. If `None` is given a virtual
            instrument is created.
        lia_terminator : str
            Termination character for communication with the lock-in amplifier.
        lia_baud : int
            Baud rate for serial communication with the lock-in amplifier.
        lia_output_interface : int
            Communication interface on the lock-in amplifier rear panel used to read
            instrument responses. This does not need to match the VISA resource
            interface type if, for example, an interface adapter is used between the
            control computer and the instrument. Valid output communication interfaces:
                * 0 : RS232
                * 1 : GPIB
        """
        if dummy is True:
            self.lia = virtual_sr830.sr830(return_int=True)
        elif lia_address is None:
            return
        else:
            self.lia = sr830.sr830(return_int=True, check_errors=True)

        # default lia_output_interface is RS232
        self.lia.connect(
            resource_name=lia_address,
            output_interface=lia_output_interface,
            set_default_configuration=True,
        )
        self.lia_idn = self.lia.get_id()

    def connect_monochromator(
        self,
        dummy=False,
        visa_lib="@py",
        mono_address=None,
        mono_terminator="\r",
        mono_baud=9600,
    ):
        """Create monochromator connection.

        Parameters
        ----------
        dummy : bool
            Choose whether or not to make all instruments virtual. Useful for testing
            control logic.
        visa_lib : str
            PyVISA backend.
        mono_address : str
            VISA resource name for the monochromator. If `None` is given a virtual
            instrument is created.
        mono_terminator : str
            Termination character for communication with the monochromator.
        mono_baud : int
            Baud rate for serial communication with the monochromator.
        """
        if dummy is True:
            self.mono = virtual_sp2150.sp2150()
        elif mono_address is None:
            return
        else:
            self.mono = sp2150.sp2150()
        self.mono.connect(resource_name=mono_address)
        self.mono.set_scan_speed(1000)

    def connect_solarsim(self, dummy=False, visa_lib="@py", light_address=None):
        """Create solar simulator connection.

        Parameters
        ----------
        dummy : bool
            Choose whether or not to make all instruments virtual. Useful for testing
            control logic.
        visa_lib : str
            PyVISA backend.
        light_address : str
            VISA resource name for the light engine. If `None` is given a virtual
            instrument is created.
        """
        if dummy is True:
            self.le = virt.illumination()
        elif light_address is None:
            return
        else:
            self.le = illumination(address=light_address)
        self.le.connect()

    def connect_controller(
        self, dummy=False, controller_address=None,
    ):
        """Create mux and stage controller connection.

        Parameters
        ----------
        dummy : bool
            Choose whether or not to make all instruments virtual. Useful for testing
            control logic.
        controller_address : str
            VISA resource name for the multiplexor and stage controller. If `None` is
            given a virtual instrument is created.
        """
        if dummy is True:
            self.controller = virt.controller()
        elif controller_address is None:
            pass
        else:
            self.controller = controller(address=controller_address)

        self.controller.connect()

    def connect_psu(
        self,
        dummy=False,
        visa_lib="@py",
        psu_address=None,
        psu_terminator="\r",
        psu_baud=9600,
    ):
        """Create LED PSU connection.

        Parameters
        ----------
        dummy : bool
            Choose whether or not to make all instruments virtual. Useful for testing
            control logic.
        visa_lib : str
            PyVISA backend.
        psu_address : str
            VISA resource name for the LED power supply unit. If `None` is given a
            virtual instrument is created.
        psu_terminator : str
            Termination character for communication with the power supply unit.
        psu_baud : int
            Baud rate for serial communication with the power supply unit.
        """
        if dummy is True:
            self.psu = virtual_dp800.dp800()
        elif psu_address is None:
            return
        else:
            self.psu = dp800.dp800()

        self.psu.connect(resource_name=psu_address)
        self.psu_idn = self.psu.get_id()

    def connect_instruments(
        self,
        dummy=False,
        visa_lib="@py",
        smu_address=None,
        smu_terminator="\n",
        smu_baud=57600,
        smu_front_terminals=False,
        smu_two_wire=False,
        controller_address=None,
        light_address=None,
        lia_address=None,
        lia_terminator="\r",
        lia_baud=9600,
        lia_output_interface=0,
        mono_address=None,
        mono_terminator="\r",
        mono_baud=9600,
        psu_address=None,
        psu_terminator="\r",
        psu_baud=9600,
    ):
        """Connect to instruments.

        If any instrument addresses are `None`, virtual (fake) instruments are
        "connected" instead.

        Parameters
        ----------
        dummy : bool
            Choose whether or not to make all instruments virtual. Useful for testing
            control logic.
        visa_lib : str
            PyVISA backend.
        smu_address : str
            VISA resource name for the source-measure unit. If `None` is given a
            virtual instrument is created.
        smu_terminator : str
            Termination character for communication with the source-measure unit.
        smu_baud : int
            Baud rate for serial communication with the source-measure unit.
        smu_front_terminals : bool
            Flag whether to use the front terminals of the source-measure unit.
        smu_two_wire : bool
            Flag whether to measure in two-wire mode. If `False` measure in four-wire mode.
        controller_address : str
            VISA resource name for the multiplexor and stage controller. If `None` is
            given a virtual instrument is created.
        light_address : str
            VISA resource name for the light engine. If `None` is given a virtual
            instrument is created.
        lia_address : str
            VISA resource name for the lock-in amplifier. If `None` is given a virtual
            instrument is created.
        lia_terminator : str
            Termination character for communication with the lock-in amplifier.
        lia_baud : int
            Baud rate for serial communication with the lock-in amplifier.
        lia_output_interface : int
            Communication interface on the lock-in amplifier rear panel used to read
            instrument responses. This does not need to match the VISA resource
            interface type if, for example, an interface adapter is used between the
            control computer and the instrument. Valid output communication interfaces:
                * 0 : RS232
                * 1 : GPIB
        mono_address : str
            VISA resource name for the monochromator. If `None` is given a virtual
            instrument is created.
        mono_terminator : str
            Termination character for communication with the monochromator.
        mono_baud : int
            Baud rate for serial communication with the monochromator.
        psu_address : str
            VISA resource name for the LED power supply unit. If `None` is given a
            virtual instrument is created.
        psu_terminator : str
            Termination character for communication with the power supply unit.
        psu_baud : int
            Baud rate for serial communication with the power supply unit.
        """
        self.connect_smu(
            dummy=dummy,
            visa_lib=visa_lib,
            smu_address=smu_address,
            smu_terminator=smu_terminator,
            smu_baud=smu_baud,
            smu_front_terminals=smu_front_terminals,
            smu_two_wire=smu_two_wire,
        )

        self.connect_lia(
            dummy=dummy,
            visa_lib=visa_lib,
            lia_address=lia_address,
            lia_terminator=lia_terminator,
            lia_baud=lia_baud,
            lia_output_interface=lia_output_interface,
        )

        self.connect_monochromator(
            dummy=dummy,
            visa_lib=visa_lib,
            mono_address=mono_address,
            mono_terminator=mono_terminator,
            mono_baud=mono_baud,
        )

        self.connect_solarsim(
            dummy=dummy, visa_lib=visa_lib, light_address=light_address
        )

        self.connect_controller(
            dummy=dummy, controller_address=controller_address,
        )

        self.connect_psu(
            dummy=dummy,
            visa_lib=visa_lib,
            psu_address=psu_address,
            psu_terminator=psu_terminator,
            psu_baud=psu_baud,
        )

    def disconnect_all_instruments(self):
        """Disconnect all instruments."""
        self.sm.disconnect()
        self.lia.disconnect()
        self.mono.disconnect()
        self.controller.disconnect()
        self.le.disconnect()
        self.psu.disconnect()

    def hardware_test(self, substrates_to_test):
        """Test hardware."""
        pass

    def measure_spectrum(self, recipe=None):
        """Measure the spectrum of the light source.

        Uses the internal spectrometer.

        Parameters
        ----------
        recipe : str
            Name of the spectrum recipe for the light source to load.

        Returns
        -------
        raw_spectrum : list
            Raw spectrum measurements in arbitrary units.
        """
        if recipe is not None:
            # choose the recipe
            self.le.light_engine.activateRecipe(recipe)

        # edit the recipe for the intensity measurement but store old values so it
        # can be changed back after
        old_duration = self.le.light_engine.getRecipeParam(param="Duration")
        new_duration = 1
        self.le.light_engine.setRecipeParam(param="Duration", value=new_duration * 1000)

        # run recipe
        run_ID = self.le.light_engine.on()
        self.le.light_engine.waitForRunFinished(run_ID=run_ID)
        self.le.light_engine.waitForResultAvailable(run_ID=run_ID)

        # get spectrum data
        raw_spectrum = self.le.light_engine.getDataSeries(run_ID=run_ID)[0]
        wls = raw_spectrum["data"]["Wavelenght"]
        raw_irr = raw_spectrum["data"]["Irradiance"]
        data = np.array([[w, i] for w, i in zip(wls, raw_irr)])

        # reset recipe
        self.le.light_engine.setRecipeParam(param="Duration", value=old_duration)

        return data.tolist()

    def run_done(self):
        """Turn off light engine and smu."""
        self.le.off()
        self.sm.outOn(on=False)

    def pixel_setup(self, pixel, handler=None, handler_kwargs={}):
        """Move to pixel and connect it with mux.

        Parameters
        ----------
        pixel : dict
            Pixel information
        """
        if pixel["position"] is not None:
            self.goto_stage_position(
                pixel["position"], handler=handler, handler_kwargs=handler_kwargs
            )

        # connect pixel
        if pixel["array_loc"] is not None:
            row = pixel["array_loc"][0]
            col = pixel["array_loc"][1]
            self.controller.set_mux(row, col, pixel["pixel"])
        else:
            self.controller.clear_mux()

    def slugify(self, value, allow_unicode=False):
        """Convert string to slug.

        Convert to ASCII if 'allow_unicode' is False. Convert spaces to hyphens.
        Remove characters that aren't alphanumerics, underscores, or hyphens.
        Convert to lowercase. Also strip leading and trailing whitespace.

        Parameters
        ----------
        value : str
            String to slugify.
        """
        value = str(value)
        if allow_unicode:
            value = unicodedata.normalize("NFKC", value)
        else:
            value = (
                unicodedata.normalize("NFKD", value)
                .encode("ascii", "ignore")
                .decode("ascii")
            )
        value = re.sub(r"[^\w\s-]", "", value).strip().lower()

        return re.sub(r"[-\s]+", "-", value)

    def steady_state(
        self,
        t_dwell=10,
        NPLC=10,
        stepDelay=0.005,
        sourceVoltage=True,
        compliance=0.04,
        setPoint=0,
        senseRange="f",
        handler=None,
        handler_kwargs={},
    ):
        """Make steady state measurements.

        for t_dwell seconds
        set NPLC to -1 to leave it unchanged returns array of measurements.

        Parameters
        ----------
        t_dwell : float
            Dwell time in seconds.
        NPLC : float
            Number of power line cycles to integrate over.
        stepDelay : float
            Step delay in seconds.
        sourceVoltage : bool
            Choose whether or to dwell at constant voltage (True) or constant current
            (False).
        compliance : float
            Compliance voltage (in V, if sourcing current) or current (in A, if
            sourcing voltage).
        setPoint : float
            Constant or voltage or current to source.
        senseRange : "a" or "f"
            Range setting: "a" for autorange, "f" for follow compliance.
        handler : handler object
            Handler to process data.
        """
        if NPLC != -1:
            self.sm.setNPLC(NPLC)
        self.sm.setStepDelay(stepDelay)
        self.sm.setupDC(
            sourceVoltage=sourceVoltage,
            compliance=compliance,
            setPoint=setPoint,
            senseRange=senseRange,
        )
        self.sm.write(
            ":arm:source immediate"
        )  # this sets up the trigger/reading method we'll use below
        q = self.sm.measureUntil(
            t_dwell=t_dwell, handler=handler, handler_kwargs=handler_kwargs
        )

        return q

    def sweep(
        self,
        sourceVoltage=True,
        senseRange="f",
        compliance=0.04,
        nPoints=1001,
        stepDelay=0.005,
        start=1,
        end=0,
        NPLC=1,
        handler=None,
        handler_kwargs={},
    ):
        """Perform I-V measurement sweep.

        Make a series of measurements while sweeping the sourcemeter along linearly
        progressing voltage or current setpoints.
        """
        self.sm.setNPLC(NPLC)
        self.sm.setStepDelay(stepDelay)
        self.sm.setupSweep(
            sourceVoltage=sourceVoltage,
            compliance=compliance,
            nPoints=nPoints,
            start=start,
            end=end,
            senseRange=senseRange,
        )

        raw = self.sm.measure()

        if handler is not None:
            handler(raw, **handler_kwargs)

        return raw

    def track_max_power(
        self,
        duration=30,
        NPLC=-1,
        step_delay=-1,
        extra="basic://7:10",
        handler=None,
        handler_kwargs={},
    ):
        """Track maximum power point.

        Parameters
        ----------
        duration : float or int
            Length of time to track max power for in seconds.
        NPLC : float or int
            Number of power line cycles. If -1, keep using previous setting.
        step_delay : float or int
            Settling delay. If -1, set to auto.
        extra : str
            Extra protocol settings to pass to mppt.
        handler : handler object
            Handler with handle_data method to process data.
        """
        message = "Tracking maximum power point for {:} seconds".format(duration)

        raw = self.mppt.launch_tracker(
            duration=duration,
            NPLC=NPLC,
            step_delay=step_delay,
            extra=extra,
            handler=handler,
            handler_kwargs=handler_kwargs,
        )

        return raw

    def eqe(
        self,
        psu_ch1_voltage=0,
        psu_ch1_current=0,
        psu_ch2_voltage=0,
        psu_ch2_current=0,
        psu_ch3_voltage=0,
        psu_ch3_current=0,
        smu_voltage=0,
        start_wl=350,
        end_wl=1100,
        num_points=76,
        grating_change_wls=None,
        filter_change_wls=None,
        integration_time=8,
        auto_gain=True,
        auto_gain_method="user",
        handler=None,
        handler_kwargs={},
    ):
        """Run an EQE scan.

        Paremeters
        ----------
        psu_ch1_voltage : float, optional
            PSU channel 1 voltage.
        psu_ch1_current : float, optional
            PSU channel 1 current.
        psu_ch2_voltage : float, optional
            PSU channel 2 voltage.
        psu_ch2_current : float, optional
            PSU channel 2 current.
        psu_ch3_voltage : float, optional
            PSU channel 3 voltage.
        psu_ch3_current : float, optional
            PSU channel 3 current.
        start_wl : int or float, optional
            Start wavelength in nm.
        end_wl : int or float, optional
            End wavelength in nm
        num_points : int, optional
            Number of wavelengths in scan
        grating_change_wls : list or tuple of int or float, optional
            Wavelength in nm at which to change to the grating.
        filter_change_wls : list or tuple of int or float, optional
            Wavelengths in nm at which to change filters
        integration_time : int
            Integration time setting for the lock-in amplifier.
        auto_gain : bool, optional
            Automatically choose sensitivity.
        auto_gain_method : {"instr", "user"}, optional
            If auto_gain is True, method for automatically finding the correct gain
            setting. "instr" uses the instrument auto-gain feature, "user" implements
            a user-defined algorithm.
        handler : data_handler object, optional
            Object that processes live data produced during the scan.
        handler_kwargs : dict, optional
            Dictionary of keyword arguments to pass to the handler.
        """
        eqe_data = eqe.scan(
            self.lia,
            self.mono,
            self.psu,
            self.sm,
            psu_ch1_voltage,
            psu_ch1_current,
            psu_ch2_voltage,
            psu_ch2_current,
            psu_ch3_voltage,
            psu_ch3_current,
            smu_voltage,
            start_wl,
            end_wl,
            num_points,
            grating_change_wls,
            filter_change_wls,
            integration_time,
            auto_gain,
            auto_gain_method,
            handler,
            handler_kwargs,
        )

        return eqe_data

    def calibrate_psu(self, channel=1, max_current=1.0, current_step=0.1):
        """Calibrate the LED PSU.

        Measure the short-circuit current of a photodiode generated upon illumination
        with an LED powered by the PSU as function of PSU current.

        Parameters
        ----------
        channel : {1, 2, 3}
            PSU channel.
        max_current : float
            Maximum current in amps to measure to.
        current_step : float
            Current step in amps.
        """
        currents = np.linspace(
            0, max_current, (max_current / current_step) + 1, endpoint=True
        )

        # set smu to short circuit and enable output
        self.sm.setupDC(
            sourceVoltage=True, compliance=0.1, setPoint=0, senseRange="a",
        )
        self.sm.outOn(True)

        # set up PSU
        self.psu.set_apply(channel=channel, voltage="MAX", current=0)
        self.psu.set_output_enable(True, channel)

        data = []
        for current in currents:
            self.psu.set_apply(channel=channel, voltage="MAX", current=current)
            time.sleep(1)
            measurement = self.sm.measure()
            measurement.append(current)
            data.append(measurement)

        # disable PSU
        self.psu.set_apply(channel=channel, voltage="MAX", current=0)
        self.psu.set_output_enable(False, channel)

        # disable smu
        self.sm.outOn(False)

        return data

    def home_stage(self, expected_lengths, timeout=120, length_poll_sleep=0.1):
        """Home the stage.

        Parameters
        ----------
        expected_lengths : list of int
            Expected lengths of each stage in steps.
        timeout : float
            Timeout in seconds. Raise an error if it takes longer than expected to home
            the stage.
        length_poll_sleep : float
            Time to wait in seconds before polling the current length of the stage to
            determine whether homing has finished.

        Returns
        -------
        response : dict
            Dictionary containing return code, message, and lengths. Error codes are
            defined as follows:

                * -1 : Timeout error.
                * -2 : Programming error.
                * -3 : Unexpected axis length. Probably stalled during homing.
                * -4 : Response error.
        """
        for i, l in enumerate(expected_lengths):
            r = self.controller.home(i + 1)
            if r != "":
                msg = f"Homing the stage along axis {i + 1} failed: {r}."
                lengths = []
                code = -4
                return {"code": code, "msg": msg, "lengths": lengths}
            elif self.controller.tn.empty_response is True:
                msg = "Programming error"
                lengths = []
                code = -2
                return {"code": code, "msg": msg, "lengths": lengths}

        t0 = time.time()
        dt = 0
        lengths = []
        while dt < timeout:
            for i, l in enumerate(expected_lengths):
                lengths[i] = self.controller.get_length(i + 1)
                if (lengths[i] > 0) & (round_sf(lengths[i], 1) != round_sf(l, 1)):
                    # if a stage length is returned but it's unexpected there was
                    # probably a stall
                    msg = (
                        f"Returned {lengths[i]} steps for axis {i + 1} but was "
                        + f"expecting {l} steps. The stage probably stalled."
                    )
                    code = -3
                    return {"code": code, "msg": msg, "lengths": lengths}
            if all([l > 0 for l in lengths]):
                # axis lengths have been returned for all axes
                msg = f"Axis lengths are {lengths} steps, as expected."
                code = 0
                return {"code": code, "msg": msg, "lengths": lengths}
            time.sleep(length_poll_sleep)
            dt = time.time() - t0

        msg = "Timeout error"
        code = -2
        return {"code": code, "msg": msg, "lengths": lengths}

    def read_stage_position(self, axes, handler=None, handler_kwargs={}):
        """Read the current stage position along all available axes.

        Parameters
        ----------
        axes : int
            Number of available stage axes.
        handler : handler object
            Handler with handle_data method to process stage position data.

        Returns
        -------
        loc : list of int
            Stage position in steps along each axis.
        """
        loc = []
        for axis in range(1, axes + 1, 1):
            loc.append(self.controller.get_position(axis))

        if handler is not None:
            handler(loc, **handler_kwargs)

        return loc

    def goto_stage_position(
        self,
        position,
        timeout=60,
        position_poll_sleep=0.5,
        handler=None,
        handler_kwargs={},
    ):
        """Go to stage position in steps.

        Uses polling to determine when stage motion is complete.

        Parameters
        ----------
        position : list of int
            Number of steps along each available stage to move to.
        timeout : float
            Timeout in seconds. Raise an error if it takes longer than expected to
            reach the required position.
        position_poll_sleep : float
            Time to wait in s before polling the current position of the stage to
            determine whether the required position has been reached.
        handler : handler object
            Handler to pass to read_stage_position method to process stage position
            during goto.
        handler_kwargs : dict
            Keyword arguments to pass to data handler.

        Returns
        -------
        response : dict
            Return value:

                * 0 : Reached position successfully.
                * -1 : Command not accepted. Stage probably isn't homed / has stalled.
                * -2 : Max retries / timeout exceeded.
                * -3 : Programming error.
        """
        # must be a whole number of steps
        position = [round(p) for p in position]

        for i, pos in enumerate(position):
            r = self.controller.goto(i + 1, pos)
            if r != "":
                msg = "Command not accepted. Stage probably isn't homed / has stalled."
                code = -1
                return {"msg": msg, "code": code}
            elif self.controller.tn.empty_response is True:
                msg = "Programming error"
                code = -3
                return {"msg": msg, "code": code}

        # goto commands accepted
        loc = []
        t0 = time.time()
        now = 0
        # periodically poll for position
        while (loc != position) and (now < timeout):
            # ask for current position
            loc = self.read_stage_position(len(position), handler, handler_kwargs)
            time.sleep(position_poll_sleep)
            now = time.time() - t0
        # exited above loop because of microtimeout, retry
        if now > timeout:
            msg = "Goto timeout exceeded."
            code = -2
            return {"msg": msg, "code": code}
        else:
            # we got there
            msg = "Reached position successfully."
            code = 0
            return {"msg": msg, "code": code}

    def check_all_contacts(
        self, rows, columns, pixels, handler=None, handler_kwargs={}
    ):
        """Perform contact check on all pixels in the system.

        Parameters
        ----------
        rows : int
            Number of rows in the substrate array.
        columns : int
            Number of columns in the substrate array.
        pixels : int
            Number of pixels on each pcb adapter in the system. Assumes all adapaters
            are the same.
        handler : handler callback
            Handler that acts on failed contact check reports.
        handler_kwargs : dict
            Keyword arguments required by the handler.

        Returns
        -------
        fail_bitmask : str
            Pass/fail bitmask where bits corresponding to pixels that failed the
            contact check are set to 1.
        """
        mux_list = [
            [r + 1, c + 1, p + 1]
            for r in range(rows)
            for c in range(columns)
            for p in range(pixels)
        ]

        failed = 0
        for r, c, p in mux_list:
            self.controller.set_mux(r, c, p)
            if self.sm.contact_check() is True:
                failed += 1
                if handler is not None:
                    handler(
                        f"Contact check FAILED! Row: {r}, col: {c}, pixel: {p}",
                        **handler_kwargs,
                    )

        return f"{failed}/{len(mux_list)} pixels failed the contact check."


def round_sf(x, sig_fig):
    """Round a number to a given significant figure.

    Paramters
    ---------
    x : float or int
        Number to round.
    sig_fig : int
        Significant figures to round to.

    Returns
    -------
    y : float
        Rounded number
    """
    return round(x, sig_fig - int(np.floor(np.log10(abs(x)))) - 1)
