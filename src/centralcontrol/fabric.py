#!/usr/bin/env python3
"""High level experiment functions."""

import numpy as np
from scipy.constants import k, e
import unicodedata
import re
import time
import sys
from pathlib import Path

# this boilerplate code allows this module to be run directly as a script
if (__name__ == "__main__") and (__package__ in [None, ""]):
    __package__ = "centralcontrol"
    # get the dir that holds __package__ on the front of the search path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from . import virt
from .mppt import mppt
from .illumination import illumination

from m1kTCPClient import m1kTCPClient


class fabric(object):
    """Experiment control logic."""

    # expecting mqtt queue publisher object
    _mqttc = None

    # keep track of connected instruments
    _connected_instruments = []

    # current_limit = float("inf")
    current_limit = 0.025  # always safe default

    # assumed temperature
    T = 300

    def __init__(self):
        """Get software revision."""
        # self.software_revision = __version__
        # print("Software revision: {:s}".format(self.software_revision))

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

    def compliance_current_guess(self, area=None, jmax=None, imax=None):
        """Guess what the compliance current should be for i-v-t measurements.

        area in cm^2
        jmax in mA/cm^2
        imax in A (overrides jmax/area calc)
        returns value in A (defaults to 0.025A = 0.5cm^2 * 50 mA/cm^2)
        """
        # default guess is a 0.5 sqcm device operating at just above the SQ limit for Si
        ret_val = 0.5 * 0.05

        # override if args set
        if imax is not None:
            ret_val = imax
        elif (area is not None) and (jmax is not None):
            ret_val = jmax * area / 1000  # scale mA to A

        # enforce the global current limit
        if ret_val > self.current_limit:
            print(
                "Warning: Detected & denied an attempt to damage equipment through "
                + "overcurrent"
            )
            ret_val = self.current_limit

        return ret_val

    def _connect_smu(
        self,
        smu_address,
        smu_port=2101,
        smu_terminator="\n",
        smu_plf=50,
        smu_two_wire=True,
    ):
        """Create smu connection.

        Parameters
        ----------
        smu_address : str
            IP address for the source-measure unit.
        smu_port : int
            Port for the SMU.
        smu_terminator : str
            Termination character for communication with the source-measure unit.
        smu_plf : float
            Power line frequency in Hz.
        smu_two_wire : bool
            Flag whether to measure in two-wire mode. If `False` measure in four-wire
            mode.
        """
        self.sm = m1kTCPClient(smu_address, smu_port, smu_terminator, smu_plf)
        self.sm_idn = self.sm.get_id()

        # set up smu terminals
        self.sm.configure_channel_settings(four_wire=not (smu_two_wire))

        # apply external calibration to all smu channels
        self.sm.use_external_calibration()

        # instantiate max-power tracker object based on smu
        self.mppt = mppt(self.sm, self.current_limit)

        self._connected_instruments.append(self.sm)

    def _connect_solarsim(self, is_virt=False, light_address=None, light_recipe=None):
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
        if is_virt is True:
            self.le = virt.illumination(
                address=light_address, default_recipe=light_recipe
            )
        else:
            self.le = illumination(address=light_address, default_recipe=light_recipe)
        self.le.connect()

        self._connected_instruments.append(self.le)

    def connect_instruments(
        self,
        smu_address=None,
        smu_port=2101,
        smu_terminator="\n",
        smu_plf=50,
        smu_two_wire=True,
        light_address=None,
        light_virt=False,
        light_recipe=None,
    ):
        """Connect to instruments.

        If any instrument addresses are `None`, virtual (fake) instruments are
        "connected" instead.

        Parameters
        ----------
        smu_address : str
            IP address for the source-measure unit.
        smu_port : int
            Port for the SMU.
        smu_terminator : str
            Termination character for communication with the source-measure unit.
        smu_plf : float
            Power line frequency in Hz.
        smu_two_wire : bool
            Flag whether to measure in two-wire mode. If `False` measure in four-wire
            mode.
        light_address : str
            VISA resource name for the light engine. If `None` is given a virtual
            instrument is created.
        light_recipe : str
            Recipe name.
        """
        if smu_address is not None:
            self._connect_smu(
                smu_address=smu_address,
                smu_port=smu_port,
                smu_terminator=smu_terminator,
                smu_plf=smu_plf,
                smu_two_wire=smu_two_wire,
            )

        if light_address is not None:
            self._connect_solarsim(
                is_virt=light_virt,
                light_address=light_address,
                light_recipe=light_recipe,
            )

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
        if hasattr(self, "le"):
            self.le.off()
        if hasattr(self, "sm"):
            self.sm.enable_output(False)

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
        nplc=-1,
        settling_delay=-1,
        source_voltage=True,
        set_point=0,
        pixels={},
        handler=lambda x: None,
    ):
        """Make steady state measurements.

        for t_dwell seconds
        set NPLC to -1 to leave it unchanged returns array of measurements.

        Parameters
        ----------
        t_dwell : float
            Dwell time in seconds.
        nplc : float
            Number of power line cycles to integrate over.
        settling_delay : float
            Settling delay in seconds.
        source_voltage : bool
            Choose whether or to dwell at constant voltage (True) or constant current
            (False).
        set_point : float
            Constant or voltage or current to source.
        pixels : dict
            Pixel information dictionary. Keys are SMU channel numbers.
        handler : handler object
            Handler to process data.

        Returns
        -------
        data : dict
            Dictionary of SMU measurement data. Dictionary keys are channel numbers.
        """
        if nplc != -1:
            self.sm.nplc = nplc

        if settling_delay != -1:
            self.sm.settling_delay = settling_delay

        if source_voltage is True:
            source_mode = "v"
        else:
            source_mode = "i"

        channels = [ch for ch, _ in pixels.items()]

        if (source_mode == "i") and (set_point == 0):
            # measuring at Voc so set smu outputs to high impedance mode
            self.sm.enable_output(False, channels)
        else:
            # configure smu outputs and enable them
            values = {}
            for ch in channels:
                values[ch] = set_point
            self.sm.configure_dc(values, source_mode)
            self.sm.enable_output(True, channels)

        # init container for all data
        ss_data = {}
        for ch in channels:
            ss_data[ch] = []

        # run steady state measurement
        t0 = time.time()
        while time.time() - t0 < t_dwell:
            data = self.sm.measure(channels, measurement="dc")
            handler(data)
            for ch, ch_data in sorted(data.items()):
                ss_data[ch].extend(ch_data)

        return ss_data

    def sweep(
        self,
        nplc=-1,
        settling_delay=-1,
        start=1,
        end=0,
        points=101,
        source_voltage=True,
        smart_compliance=True,
        pixels={},
        handler=lambda x: None,
    ):
        """Perform I-V measurement sweep.

        Make a series of measurements while sweeping the sourcemeter along linearly
        progressing voltage or current setpoints.
        """
        if nplc != -1:
            self.sm.nplc = nplc

        if settling_delay != -1:
            self.sm.settling_delay = settling_delay

        if source_voltage is True:
            source_mode = "v"
        else:
            source_mode = "i"

        step = (end - start) / (points - 1)
        rvalues = [x * step + start for x in range(points)]

        # measure voc's of devices to estimate compliance voltage
        if smart_compliance is True:
            ssvocs = self.steady_state(
                t_dwell=0.1,
                nplc=-1,
                settling_delay=-1,
                source_voltage=False,
                set_point=0,
                pixels=pixels,
            )

            max_vs = {}
            for ch, ssvoc in sorted(ssvocs.items()):
                area = pixels[ch]["area"]
                max_v = smart_compliance(ssvoc[0][0], self.current_limit, area)
                max_vs[ch] = max_v

            values = {}
            for ch, max_v in max_vs.items():
                values[ch] = [v if v < max_v else max_v for v in rvalues]
        else:
            values = {}
            for ch, _ in pixels.items():
                values[ch] = rvalues

        self.sm.configure_list_sweep(values=values, source_mode=source_mode)

        # get and set initial values then enable outputs
        channels = [ch for ch, _ in pixels.items()]
        init_values = {}
        for ch, vs in values.items():
            init_values[ch] = vs[0]
        self.sm.configure_dc(init_values, source_mode)
        self.sm.enable_output(True, channels)

        # perform measurement
        data = self.sm.measure(channels, measurement="sweep")
        handler(data)

        return data

    def smart_compliance(self, voc, compliance_i, area):
        """Calculate compliance voltage given compliance current.

        Use the Voc of a solar cell to estimate the maximum voltage that can be safely
        applied to it assuming it behaves like an ideal diode.

        Parameters
        ----------
        voc : float
            Open-circuit voltage in V.
        compliance_i : float
            Compliance current in A.
        area : float
            Device area in cm^2.

        Returns
        -------
        max_v : float
            Maximum voltage that can be applied safely in V.
        """
        compliance_j = compliance_i / area

        # approximate ideal short circuit for Si
        ideal_j = 50

        # thermal voltage
        vt = k * self.T / e

        return vt * np.log((1 - (-compliance_j) / ideal_j) * (np.exp(voc / vt) - 1) + 1)

    def track_max_power(
        self,
        duration=30,
        NPLC=-1,
        extra="basic://7:10",
        pixels={},
        handler=lambda x: None,
        voc_compliance=3,
        i_limit=0.04,
    ):
        """Track maximum power point.

        Parameters
        ----------
        duration : float or int
            Length of time to track max power for in seconds.
        NPLC : float or int
            Number of power line cycles. If -1, keep using previous setting.
        extra : str
            Extra protocol settings to pass to mppt.
        pixels : dict
            Pixel information dictionary. Keys are SMU channel numbers.
        handler : handler object
            Handler with handle_data method to process data.
        """
        raw = self.mppt.launch_tracker(
            duration=duration,
            NPLC=NPLC,
            extra=extra,
            callback=handler,
            voc_compliance=voc_compliance,
            i_limit=i_limit,
            pixels=pixels,
        )
        self.mppt.reset()

        return raw


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
    pass
