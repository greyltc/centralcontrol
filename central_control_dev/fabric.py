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

import central_control_dev.virt as virt
from central_control_dev.k2400 import k2400
from central_control_dev.pcb import pcb
from central_control_dev.motion import motion
from central_control_dev.mppt import mppt
from central_control_dev.illumination import illumination
import central_control_dev  # for __version__

import sr830
import sp2150
import dp800
import virtual_sr830
import virtual_sp2150
import virtual_dp800
import eqe


class fabric:
    """Experiment control logic."""

    # expecting mqtt queue publisher object
    _mqttc = None

    # keep track of connected instruments
    _connected_instruments = []

    def __init__(self):
        """Get software revision."""
        # self.software_revision = __version__
        # print("Software revision: {:s}".format(self.software_revision))
        pass

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context related to this object.

        Make sure everything gets cleaned up properly.
        """
        print("exiting...")
        self.disconnect_all_instruments()
        print("cleaned up successfully")

    def compliance_current_guess(self, area=None):
        """Guess what the compliance current should be for i-v-t measurements.

        Parameters
        ----------
        area : float
            Device area in cm^2.
        """
        # set maximum current density (in mA/cm^2) 
        #max_j = 50  # slightly higher than an ideal Si cell
        max_j = 100  # as per request to mod
        
        hard_limit = 0.1 # A

        # calculate equivalent current in A for given device area
        if area is None:
            compliance_i = 0.05  # no area info given so can't make a calcualted guess
        else:
            compliance_i = max_j * area / 1000
        
        # don't allow even large area devices to go above 100mA
        if compliance_i > hard_limit:
            compliance_i = hard_limit

        print(f"compliance for pixel with area={area}cm^2 set to {compliance_i/1000}mA")

        return compliance_i

    def _connect_smu(
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
            Flag whether to measure in two-wire mode. If `False` measure in four-wire
            mode.
        """
        t0 = time.time()
        if dummy is True:
            self.sm = virt.k2400()
        else:
            self.sm = k2400(
                visa_lib=visa_lib,
                terminator=smu_terminator,
                addressString=smu_address,
                serialBaud=smu_baud,
            )
        self.sm_idn = self.sm.idn
        print(f"SMU connect time = {time.time() - t0} s")

        # set up smu terminals
        self.sm.setTerminals(front=smu_front_terminals)
        self.sm.setWires(twoWire=smu_two_wire)

        # instantiate max-power tracker object based on smu
        self.mppt = mppt(self.sm)

        self._connected_instruments.append(self.sm)

    def _connect_lia(
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
        else:
            self.lia = sr830.sr830()

        # default lia_output_interface is RS232
        self.lia.connect(
            lia_address, output_interface=lia_output_interface, **{"timeout": 90000},
        )
        print(self.lia.idn)

        self._connected_instruments.append(self.lia)

    def _connect_monochromator(
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
        else:
            self.mono = sp2150.sp2150()
        self.mono.connect(resource_name=mono_address, **{"timeout": 10000})

        self._connected_instruments.append(self.mono)

    def _connect_solarsim(
        self, dummy=False, visa_lib="@py", light_address=None, light_recipe=None
    ):
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
            self.le = virt.illumination(
                address=light_address, default_recipe=light_recipe
            )
        else:
            self.le = illumination(address=light_address, default_recipe=light_recipe)
        self.le.connect()

        self._connected_instruments.append(self.le)

    def _connect_psu(
        self,
        dummy=False,
        visa_lib="@py",
        psu_address=None,
        psu_terminator="\r",
        psu_baud=9600,
        psu_ocps=[0.5, 0.5, 0.5],
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
        psu_ocps : list
            List overcurrent protection values in ascending channel order, one value
            per channel.
        """
        if dummy is True:
            self.psu = virtual_dp800.dp800()
        else:
            self.psu = dp800.dp800()

        self.psu.connect(resource_name=psu_address)
        self.psu_idn = self.psu.get_id()

        for i, ocp in enumerate(psu_ocps):
            self.psu.set_output_enable(False, i + 1)
            self.psu.set_ocp_value(ocp, i + 1)
            self.psu.set_ocp_enable(True, i + 1)

        self._connected_instruments.append(self.psu)

    def _connect_pcb(self, dummy=False, pcb_address=None):
        """Add control PCB attributes to class.

        PCB commands run in their own context manager so this isn't a real connect
        method. It just enables the PCB methods to function.

        Adds a class as an attribute rather than returning an object.

        Not necessary to append to list of connected instruments.

        Parameters
        ----------
        pcb_address : str
            Control PCB address string.
        """
        if dummy is True:
            self.pcb_address = "dummy"
            self.pcb = virt.pcb
        else:
            self.pcb_address = pcb_address
            self.pcb = pcb

    def _connect_motion(self, dummy=False, motion_address=None):
        """Add motion controller attributes to class.

        Motion commands run in their own context manager so this isn't a real connect
        method. It just enables the motion methods to function.

        Adds a class as an attribute rather than returning an object.

        Not necessary to append to list of connected instruments.

        Parameters
        ----------
        motion_address : str
            Control PCB address string.
        """
        if dummy is True:
            self.motion_address = "dummy"
            self.motion = virt.motion
        else:
            self.motion_address = motion_address
            self.motion = motion

    def connect_instruments(
        self,
        dummy=False,
        visa_lib="@py",
        smu_address=None,
        smu_terminator="\n",
        smu_baud=57600,
        smu_front_terminals=False,
        smu_two_wire=False,
        pcb_address=None,
        motion_address=None,
        light_address=None,
        light_recipe=None,
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
        psu_ocps=[0.5, 0.5, 0.5],
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
            Flag whether to measure in two-wire mode. If `False` measure in four-wire
            mode.
        pcb_address : str
            VISA resource name for the multiplexor and stage pcb. If `None` is
            given a virtual instrument is created.
        light_address : str
            VISA resource name for the light engine. If `None` is given a virtual
            instrument is created.
        light_recipe : str
            Recipe name.
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
        psu_ocps : list
            List overcurrent protection values in ascending channel order, one value
            per channel.
        """
        if smu_address is not None:
            self._connect_smu(
                dummy=dummy,
                visa_lib=visa_lib,
                smu_address=smu_address,
                smu_terminator=smu_terminator,
                smu_baud=smu_baud,
                smu_front_terminals=smu_front_terminals,
                smu_two_wire=smu_two_wire,
            )

        if lia_address is not None:
            self._connect_lia(
                dummy=dummy,
                visa_lib=visa_lib,
                lia_address=lia_address,
                lia_terminator=lia_terminator,
                lia_baud=lia_baud,
                lia_output_interface=lia_output_interface,
            )

        if mono_address is not None:
            self._connect_monochromator(
                dummy=dummy,
                visa_lib=visa_lib,
                mono_address=mono_address,
                mono_terminator=mono_terminator,
                mono_baud=mono_baud,
            )

        if light_address is not None:
            self._connect_solarsim(
                dummy=dummy,
                visa_lib=visa_lib,
                light_address=light_address,
                light_recipe=light_recipe,
            )

        if psu_address is not None:
            self._connect_psu(
                dummy=dummy,
                visa_lib=visa_lib,
                psu_address=psu_address,
                psu_terminator=psu_terminator,
                psu_baud=psu_baud,
                psu_ocps=psu_ocps,
            )

        if pcb_address is not None:
            self._connect_pcb(dummy, pcb_address)

        if motion_address is not None:
            self._connect_motion(dummy, motion_address)

    def disconnect_all_instruments(self):
        """Disconnect all instruments."""
        print("disconnecting instruments...")
        while len(self._connected_instruments) > 0:
            instr = self._connected_instruments.pop()
            print(instr)
            try:
                instr.disconnect()
            except:
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
        # get spectrum data
        wls, counts = self.le.get_spectrum()
        data = [[wl, count] for wl, count in zip(wls, counts)]

        return data

    def run_done(self):
        """Turn off light engine and smu."""
        self.le.off()
        self.sm.outOn(on=False)

    def goto_pixel(self, pixel):
        """Move to a pixel.

        Parameters
        ----------
        pixel : dict
            Pixel information dictionary.

        Returns
        -------
        response : int
            Response code. 0 is good, everything else means fail.
        """
        with self.pcb(self.pcb_address) as p:
            me = self.motion(self.motion_address, p)
            me.connect()
            if pixel["position"] is not None:
                resp = me.goto(pixel["position"])
            else:
                resp = 0

        return resp

    def select_pixel(self, pixel):
        """Select pixel on the mux.

        Parameters
        ----------
        pixel : dict
            Pixel information dictionary.

        Returns
        -------
        response : int
            Response code. 0 is good, everything else means fail.
        """
        with self.pcb(self.pcb_address) as p:
            # connect pixel
            if (substrate := pixel["sub_name"]) is not None:
                # open all relays
                resp = p.get("s")

                if resp == "":
                    # select the correct pixel
                    resp = p.pix_picker(substrate, pixel["pixel"])
            else:
                # open all mux relays
                resp = p.get("s")

                # get responds to s with empty string on success
                if resp == "":
                    resp = 0

            if resp is True:
                resp = 0

        return resp

    def set_experiment_relay(self, exp_relay):
        """Choose EQE or IV connection.

        Parameters
        ----------
        exp_relay : {"eqe", "iv"}
            Experiment name: either "eqe" or "iv" corresponding to relay.
        """
        resp = ""
        if "otter" in self.motion_address:  # TODO: do this in some better way
            with self.pcb(self.pcb_address) as p:
                resp = p.get(exp_relay)

        return resp

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
        sourceVoltage=True,
        compliance=0.04,
        setPoint=0,
        senseRange="f",
        handler=lambda x: None,
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
        self.sm.setupDC(
            sourceVoltage=sourceVoltage,
            compliance=compliance,
            setPoint=setPoint,
            senseRange=senseRange,
        )
        self.sm.write(
            ":arm:source immediate"
        )  # this sets up the trigger/reading method we'll use below

        raw = self.sm.measureUntil(t_dwell=t_dwell, cb=handler)

        return raw

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
        handler=lambda x: None,
    ):
        """Perform I-V measurement sweep.

        Make a series of measurements while sweeping the sourcemeter along linearly
        progressing voltage or current setpoints.
        """
        self.sm.setNPLC(NPLC)
        self.sm.setupSweep(
            sourceVoltage=sourceVoltage,
            compliance=compliance,
            stepDelay=stepDelay,
            nPoints=nPoints,
            start=start,
            end=end,
            senseRange=senseRange,
        )
        self.sm.write(":arm:source immediate")

        handler(raw := self.sm.measure(nPoints))
        return raw

    def track_max_power(
        self, duration=30, NPLC=-1, extra="basic://7:10", handler=lambda x: None
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
            duration=duration, NPLC=NPLC, extra=extra, callback=handler
        )
        self.mppt.reset()

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
        smu_compliance=0.1,
        start_wl=350,
        end_wl=1100,
        num_points=76,
        grating_change_wls=None,
        filter_change_wls=None,
        time_constant=8,
        auto_gain=True,
        auto_gain_method="user",
        handler=lambda x: None,
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
        time_constant : int
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
        self.sm.setupDC(
            sourceVoltage=True,
            compliance=smu_compliance,
            setPoint=smu_voltage,
            senseRange="f",
        )

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
            smu_compliance,
            start_wl,
            end_wl,
            num_points,
            grating_change_wls,
            filter_change_wls,
            time_constant,
            auto_gain,
            auto_gain_method,
            handler,
        )

        return eqe_data

    def calibrate_psu(
        self, channel=1, max_current=0.5, current_steps=10, max_voltage=1
    ):
        """Calibrate the LED PSU.

        Measure the short-circuit current of a photodiode generated upon illumination
        with an LED powered by the PSU as function of PSU current.

        Parameters
        ----------
        channel : {1, 2, 3}
            PSU channel.
        max_current : float
            Maximum current in amps to measure to.
        current_steps : int
            Number of current steps to measure.
        """
        # block the monochromator so there's no AC background
        self.mono.filter = 5
        self.mono.wavelength = 300

        currents = np.linspace(0, max_current, int(current_steps), endpoint=True)

        # set smu to short circuit and enable output
        self.sm.setupDC(
            sourceVoltage=True, compliance=0.1, setPoint=0, senseRange="a",
        )
        self.sm.write(":arm:source immediate")

        # set up PSU
        self.psu.set_apply(channel=channel, voltage=max_voltage, current=0)
        self.psu.set_output_enable(True, channel)

        data = []
        for current in currents:
            self.psu.set_apply(channel=channel, voltage=max_voltage, current=current)
            time.sleep(1)
            measurement = list(self.sm.measure()[0])
            measurement.append(current)
            data.append(measurement)

        # disable PSU
        self.psu.set_apply(channel=channel, voltage=max_voltage, current=0)
        self.psu.set_output_enable(False, channel)

        # disable smu
        self.sm.outOn(False)

        # unblock the monochromator
        self.mono.filter = 1
        self.mono.wavelength = 0

        return data

    def home_stage(self):
        """Home the stage."""
        with self.pcb(self.pcb_address) as p:
            me = self.motion(self.motion_address, p)
            me.connect()
            return me.home()

    def read_stage_position(self):
        """Read the current stage position along all available axes."""
        with self.pcb(self.pcb_address) as p:
            me = self.motion(self.motion_address, p)
            me.connect()
            return me.get_position()

    def goto_stage_position(
        self, position,
    ):
        """Go to stage position in steps.

        Parameters
        ----------
        position : list of float
            Position in mm along each available stage to move to.
        """
        with self.pcb(self.pcb_address) as p:
            me = self.motion(self.motion_address, p)
            me.connect()
            return me.goto(position)

    def contact_check(self, pixel_queue, handler=lambda x: None):
        """Perform contact checks on a queue of pixels.

        Parameters
        ----------
        pixel_queue : deque of dict
            Queue of pixels to check
        handler : handler callback
            Handler that acts on failed contact check reports.
        handler_kwargs : dict
            Keyword arguments required by the handler.

        Returns
        -------
        fail_msg : str
            Pass/fail summary.
        """
        failed = 0
        self.sm.setupDC(sourceVoltage=False, compliance=5, setPoint=0)
        self.sm.write(":arm:source immediate")
        with self.pcb(self.pcb_address) as p:
            while len(pixel_queue) > 0:
                # get pixel info
                pixel = pixel_queue.popleft()
                label = pixel["label"]
                pix = pixel["pixel"]

                # add id str to handlers to display on plots
                idn = f"{label}_pixel{pix}"

                if pixel["sub_name"] is not None:
                    resp = p.pix_picker(pixel["sub_name"], pixel["pixel"])
                else:
                    resp = p.get("s")

                self.sm.measure()
                if self.sm.contact_check() is True:
                    failed += 1
                    handler(f"Contact check FAILED! Device: {idn}")
        self.sm.outOn(False)
        return f"{failed} pixels failed the contact check."


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


# for testing
if __name__ == "__main__":
    with fabric() as f:
        f.connect_instruments(
            smu_address="GPIB0::24::INSTR", smu_terminator="\r", smu_baud=57600
        )
