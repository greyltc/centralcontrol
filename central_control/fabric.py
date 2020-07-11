import h5py
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

    outputFormatRevision = (
        "1.8.2"  # tells reader what format to expect for the output file
    )
    ssVocDwell = 10  # [s] dwell time for steady state voc determination
    ssIscDwell = 10  # [s] dwell time for steady state isc determination

    # start/end sweeps this many percentage points beyond Voc
    # bigger numbers here give better fitting for series resistance
    # at an incresed danger of pushing too much current through the device
    percent_beyond_voc = 50

    # guess at what the current limit should be set to (in amps) if we have no other way to determine it
    compliance_guess = 0.04

    # function to use when sending ROIs to the GUI
    update_gui = None

    def __init__(self):
        self.software_revision = central_control.__version__
        print("Software revision: {:s}".format(self.software_revision))

    def __setattr__(self, attr, value):
        """here we can override what happends when we set an attribute"""
        if attr == "Voc":
            self.__dict__[attr] = value
            if value != None:
                print("V_oc is {:.4f}mV".format(value * 1000))

        elif attr == "Isc":
            self.__dict__[attr] = value
            if value != None:
                print("I_sc is {:.4f}mA".format(value * 1000))

        else:
            self.__dict__[attr] = value

    def connect(
        self,
        dummy=False,
        visa_lib="@py",
        smu_address=None,
        smu_terminator="\n",
        smu_baud=57600,
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
        # source measure unit
        if (smu_address is None) or (dummy is True):
            self.sm = virt.k2400()
        else:
            self.sm = k2400(
                visa_lib=visa_lib,
                terminator=smu_terminator,
                addressString=smu_address,
                serialBaud=smu_baud,
            )
        self.sm_idn = self.sm.idn

        # instantiate max-power tracker object based on smu
        self.mppt = mppt(self.sm)

        # multiplexor
        if (controller_address is None) or (dummy is True):
            self.controller = virt.controller()
        else:
            self.controller = controller(address=controller_address)
        self.controller.connect()

        # light engine
        if (light_address is None) or (dummy is True):
            self.le = virt.illumination()
        else:
            self.le = illumination(address=light_address)
        self.le.connect()

        # lock=in amplifier
        if (lia_address is None) or (dummy is True):
            self.lia = virtual_sr830.sr830(return_int=True)
        else:
            self.lia = sr830.sr830(return_int=True, check_errors=True)
            # default lia_output_interface is RS232
        self.lia.connect(
            resource_name=lia_address,
            output_interface=lia_output_interface,
            set_default_configuration=True,
        )
        self.lia_idn = self.lia.get_id()

        # monochromator
        if (mono_address is None) or (dummy is True):
            self.mono = virtual_sp2150.sp2150()
        else:
            self.mono = sp2150.sp2150()
        self.mono.connect(resource_name=mono_address)
        self.mono.set_scan_speed(1000)

        # bias LED PSU
        if (psu_address is None) or (dummy is True):
            self.psu = virtual_dp800.dp800()
        else:
            self.psu = dp800.dp800()
        self.psu.connect(resource_name=psu_address)
        self.psu_idn = self.psu.get_id()

    def hardwareTest(self, substrates_to_test):
        pass

    def measureIntensity(self, recipe=None, spectrum_cal=None):
        """Measure the equivalent solar intensity of the light source.

        Uses either reference calibration diodes on the sample stage and/or the light
        source's own internal calibration sensor.

        The function can return the number of suns and ADC counts for two stage-mounted
        diodes using diode calibration values in diode_cal (if diode_cal is not a tuple
        with valid calibration values, sets intensity to 1.0 sun for both diodes.)

        This function will try to calculate the equivalent solar intensity based on a
        measurement of the spectral irradiance if supported by the light source. To
        obtain the correct units and relative scaling for the spectral irradiance
        calibration data must be supplied as an argument to the function call. If
        calibration data is not supplied the raw measurement will be returned and the
        intensity is assumed to be 1.0 sun equivalent.

        Parameters
        ----------
        recipe : str
            Name of the spectrum recipe for the light source to load.
        spectrum_cal : array-like
            Calibration data for the light source's internal spectrometer used to
            convert the raw measurement to units of spectral irradiance.

        Returns
        -------
        ret : dict
            Dictionary of intensity measurements, i.e. diode readings and/or Wavelabs
            integrated intensity.
        """
        ret = {
            "diode_1_adc": None,
            "diode_2_adc": None,
            "diode_1_suns": None,
            "diode_2_suns": None,
            "wavelabs_suns": None,
        }

        self.spectrum = None

        if self.le.wavelabs is True:
            # if using wavelabs light engine, use internal spectrometer to measure
            # spectrum and intensity
            if recipe is not None:
                # choose the recipe
                self.le.light_engine.activateRecipe(recipe)

            # edit the recipe for the intensity measurement but store old values so it
            # can be changed back after
            old_duration = self.le.light_engine.getRecipeParam(param="Duration")
            new_duration = 1
            self.le.light_engine.setRecipeParam(
                param="Duration", value=new_duration * 1000
            )
            run_ID = self.le.light_engine.on()
            self.le.light_engine.waitForRunFinished(run_ID=run_ID)
            self.le.light_engine.waitForResultAvailable(run_ID=run_ID)
            spectra = self.le.light_engine.getDataSeries(run_ID=run_ID)
            self.le.light_engine.setRecipeParam(param="Duration", value=old_duration)
            spectrum = spectra[0]
            wls = spectrum["data"]["Wavelenght"]
            irr = spectrum["data"]["Irradiance"]
            self.spectrum_raw = np.array([[w, i] for w, i in zip(wls, irr)])
            if spectrum_cal is None:
                self.spectrum = self.spectrum_raw
                ret["wavelabs_suns"] = 1
                warnings.warn("Spectral calibration not provided. Assuming 1.0 suns.")
            else:
                self.spectrum = self.spectrum_raw * spectrum_cal
                # calculate intensity in suns
                ret["wavelabs_suns"] = sp.integrare.simps(self.spectrum, wls) / 1000

        return ret

    def isWithinPercent(target, value, percent=10):
        """
    returns true if value is within percent percent of target, otherwise returns false
    """
        lb = target * (100 - percent) / 100
        ub = target * (100 + percent) / 100
        ret = False
        if lb <= value and value <= ub:
            ret = True
        return ret

    def runDone(self):
        """Turn off light engine."""
        self.le.off()

    def pixel_setup(self, pixel):
        """Move to pixel and connect it with mux.

        Parameters
        ----------
        pixel : dict
            Pixel information
        """
        # move to pixel position
        for i, pos in enumerate(pixel["position"]):
            # goto is 1-indexed
            self.controller.goto(i + 1, pos)

        # connect pixel
        row = pixel["array_loc"][0]
        col = pixel["array_loc"][1]
        self.controller.set_mux(row, col, pixel["pixel"])

    def slugify(self, value, allow_unicode=False):
        """
    Convert to ASCII if 'allow_unicode' is False. Convert spaces to hyphens.
    Remove characters that aren't alphanumerics, underscores, or hyphens.
    Convert to lowercase. Also strip leading and trailing whitespace.
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

    def insertStatus(self, message):
        """adds status message to the status message list"""
        print(message)
        s = np.array((len(self.m), message), dtype=self.status_datatype)
        self.s = np.append(self.s, s)

    def steadyState(
        self,
        t_dwell=10,
        NPLC=10,
        stepDelay=0.005,
        sourceVoltage=True,
        compliance=0.04,
        setPoint=0,
        senseRange="f",
        handler=None,
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
        self.insertStatus(
            "Measuring steady state {:s} at {:.0f} m{:s}".format(
                "current" if sourceVoltage else "voltage",
                setPoint * 1000,
                "V" if sourceVoltage else "A",
            )
        )
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
        q = self.sm.measureUntil(t_dwell=t_dwell, handler=handler)
        qa = np.array([tuple(s) for s in q], dtype=self.measurement_datatype)
        return qa

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
        message=None,
        handler=None,
    ):
        """Make a series of measurements while sweeping the sourcemeter along linearly
        progressing voltage or current setpoints."""

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

        if message == None:
            word = "current" if sourceVoltage else "voltage"
            abv = "V" if sourceVoltage else "A"
            message = "Sweeping {:s} from {:.0f} m{:s} to {:.0f} m{:s}".format(
                word, start, abv, end, abv
            )
        self.insertStatus(message)
        raw = self.sm.measure()
        sweepValues = np.array(
            list(zip(*[iter(raw)] * 4)), dtype=self.measurement_datatype
        )
        if handler is not None:
            handler(raw)

        return sweepValues

    def track_max_power(
        self,
        duration=30,
        message=None,
        NPLC=-1,
        stepDelay=-1,
        extra="basic://7:10",
        handler=None,
    ):
        if message == None:
            message = "Tracking maximum power point for {:} seconds".format(duration)
        self.insertStatus(message)
        raw = self.mppt.launch_tracker(
            duration=duration, NPLC=NPLC, extra=extra, handler=handler
        )
        # raw = self.mppt.launch_tracker(duration=duration, callback=fabric.mpptCB, NPLC=NPLC)
        qa = np.array([tuple(s) for s in raw], dtype=self.measurement_datatype)

    def mpptCB(measurement):
        """Callback function for max power point tracker
    (for live tracking)
    """
        [v, i, t, status] = measurement
        print("At {:.6f}\t{:.6f}\t{:.6f}\t{:d}".format(t, v, i, int(status)))

    def eqe(
        self,
        psu_ch1_voltage=0,
        psu_ch1_current=0,
        psu_ch2_voltage=0,
        psu_ch2_current=0,
        psu_ch3_voltage=0,
        psu_ch3_current=0,
        smu_voltage=0,
        calibration=True,
        ref_measurement_path=None,
        ref_measurement_file_header=1,
        ref_eqe_path=None,
        ref_spectrum_path=None,
        start_wl=350,
        end_wl=1100,
        num_points=76,
        repeats=1,
        grating_change_wls=None,
        filter_change_wls=None,
        auto_gain=True,
        auto_gain_method="user",
        integration_time=8,
        handler=None,
    ):
        """Run EQE scan."""

        # open all mux relays if calibrating
        if calibration is True:
            self.controller.clear_mux()

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
            calibration,
            ref_measurement_path,
            ref_measurement_file_header,
            ref_eqe_path,
            ref_spectrum_path,
            start_wl,
            end_wl,
            num_points,
            repeats,
            grating_change_wls,
            filter_change_wls,
            auto_gain,
            auto_gain_method,
            integration_time,
            handler,
        )

        eqe_data = np.array(eqe_data, dtype=self.eqe_datatype)
        self.eqe_data = eqe_data  # added to data file when pixelComplete is called

        return eqe_data

    def calibrate_psu(self, channel=1, loc=None, handler=None):
        """Calibrate the LED PSU.

        Measure the short-circuit current of a photodiode generated upon illumination
        with an LED powered by the PSU as function of PSU current.

        Parameters
        ----------
        channel : {1, 2, 3}
            PSU channel.
        loc : list
            Position of calibration photodiode along each axis.
        handler : DataHandler
            Handler to process data.
        """
        currents = np.linspace(0, 1, 11, endpoint=True)

        # move to photodiode
        if loc is not None:
            for i, l in loc:
                self.controller.goto(i + 1, l)

        # open all mux relays
        self.controller.clear_mux()

        # set smu to short circuit and enable output
        self.sm.setupDC(
            sourceVoltage=True, compliance=0.1, setPoint=0, senseRange="a",
        )
        self.sm.outOn(True)

        # set up PSU
        self.psu.set_apply(channel=channel, voltage="MAX", current=0)
        self.psu.set_output_enable(True, channel)

        for current in currents:
            self.psu.set_apply(channel=channel, voltage="MAX", current=current)
            time.sleep(1)
            data = self.sm.measure()
            data.append(current)
            if handler is not None:
                handler.handle_data(data)

        # disable PSU
        self.psu.set_apply(channel=channel, voltage="MAX", current=0)
        self.psu.set_output_enable(False, channel)

        # disable smu
        self.sm.outOn(False)

