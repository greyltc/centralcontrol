#!/usr/bin/env python3
"""High level experiment functions."""

import numpy as np
from scipy.integrate import simps
import unicodedata
import re
import time
import sys
from pathlib import Path

# this boilerplate code allows this module to be run directly as a script
if (__name__ == "__main__") and (__package__ in [None, '']):
  __package__ = "centralcontrol"
  # get the dir that holds __package__ on the front of the search path
  sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from . import virt
from .k2400 import k2400
from .pcb import pcb
from .motion import motion
from .mppt import mppt
from .illumination import illumination

import sr830
import virtual_sr830
import sp2150
import virtual_sp2150
import dp800
import virtual_dp800
import eqe

import logging
# for logging directly to systemd journal if we can
try:
  import systemd.journal
except ImportError:
  pass

class fabric(object):
  """Experiment control logic."""

  # expecting mqtt queue publisher object
  _mqttc = None

  # keep track of connected instruments
  _connected_instruments = []

  #current_limit = float("inf")
  current_limit = 0.1  # always safe default

  # a virtual pcb object
  fake_pcb = virt.pcb

  # a real pcb object
  real_pcb = pcb

  def __init__(self):
    """Get software revision."""
    # self.software_revision = __version__
    # print("Software revision: {:s}".format(self.software_revision))

    # setup logging
    self.lg = logging.getLogger(__name__)

    if not self.lg.hasHandlers():
      self.lg.setLevel(logging.DEBUG)
      # set up logging to systemd's journal if it's there
      if 'systemd' in sys.modules:
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

  def __enter__(self):
    """Enter the runtime context related to this object."""
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    """Exit the runtime context related to this object.

        Make sure everything gets cleaned up properly.
        """
    self.lg.debug("exiting...")
    self.disconnect_all_instruments()
    self.lg.debug("cleaned up successfully")

  def compliance_current_guess(self, area=None, jmax=None, imax=None):
    """Guess what the compliance current should be for i-v-t measurements.
        area in cm^2
        jmax in mA/cm^2
        imax in A (overrides jmax/area calc)
        returns value in A (defaults to 0.025A = 0.5cm^2 * 50 mA/cm^2)
        """
    ret_val = 0.5 * 0.05  # default guess is a 0.5 sqcm device operating at just above the SQ limit for Si
    if imax is not None:
      ret_val = imax
    elif (area is not None) and (jmax is not None):
      ret_val = jmax * area / 1000  #scale mA to A

    # enforce the global current limit
    if ret_val > self.current_limit:
      self.lg.warn("Detected & denied an attempt to damage equipment through overcurrent")
      ret_val = self.current_limit

    return ret_val

  def _connect_smu(self, is_virt=False, visa_lib="@py", smu_address=None, smu_terminator="\n", smu_baud=57600, smu_front_terminals=False, smu_two_wire=False):
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
    if is_virt == True:
      self.sm = virt.k2400()
    else:
      self.sm = k2400(visa_lib=visa_lib, terminator=smu_terminator, addressString=smu_address, serialBaud=smu_baud)
    self.sm_idn = self.sm.idn
    self.lg.debug(f"SMU connect time = {time.time() - t0} s")

    # set up smu terminals
    self.sm.setTerminals(front=smu_front_terminals)
    self.sm.setWires(twoWire=smu_two_wire)

    # instantiate max-power tracker object based on smu
    self.mppt = mppt(self.sm, self.current_limit)

    self._connected_instruments.append(self.sm)

  def _connect_lia(self, is_virt=False, visa_lib="@py", lia_address=None, lia_terminator="\r", lia_baud=9600, lia_output_interface=0):
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
    if is_virt == True:
      self.lia = virtual_sr830.sr830(return_int=True)
    else:
      self.lia = sr830.sr830()

    # default lia_output_interface is RS232
    self.lia.connect(lia_address, output_interface=lia_output_interface, **{"timeout": 90000})
    self.lg.debug(self.lia.idn)

    self._connected_instruments.append(self.lia)

  def _connect_monochromator(self, is_virt=False, visa_lib="@py", mono_address=None, mono_terminator="\r", mono_baud=9600):
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
    if is_virt == True:
      self.mono = virtual_sp2150.sp2150()
    else:
      self.mono = sp2150.sp2150()
    self.mono.connect(resource_name=mono_address, **{"timeout": 10000})

    self._connected_instruments.append(self.mono)

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
    if is_virt == True:
      self.le = virt.illumination(address=light_address, default_recipe=light_recipe)
    else:
      self.le = illumination(address=light_address, default_recipe=light_recipe)
    self.le.connect()

    self._connected_instruments.append(self.le)

  def _connect_psu(self, is_virt=False, visa_lib="@py", psu_address=None, psu_terminator="\r", psu_baud=9600, psu_ocps=[0.5, 0.5, 0.5]):
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
    if is_virt == True:
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

  def _connect_pcb(self, is_virt=False, pcb_address=None):
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
    self.pcb_address = pcb_address
    if is_virt == True:
      self.pcb = virt.pcb
    else:
      self.pcb = pcb

  def _connect_motion(self, is_virt=False, motion_address=None):
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
    self.motion_address = motion_address
    self.motion = motion
    if is_virt == True:
      self.motion_pcb = virt.pcb
    else:
      self.motion_pcb = pcb

  def connect_instruments(self, visa_lib="@py", smu_address=None, smu_virt=False, smu_terminator="\n", smu_baud=57600, smu_front_terminals=False, smu_two_wire=False, pcb_address=None, pcb_virt=False, motion_address=None, motion_virt=False, light_address=None, light_virt=False, light_recipe=None, lia_address=None, lia_virt=False, lia_terminator="\r", lia_baud=9600, lia_output_interface=0, mono_address=None, mono_virt=False, mono_terminator="\r", mono_baud=9600, psu_address=None, psu_virt=False, psu_terminator="\r", psu_baud=9600, psu_ocps=[0.5, 0.5, 0.5]):
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
      self._connect_smu(is_virt=smu_virt, visa_lib=visa_lib, smu_address=smu_address, smu_terminator=smu_terminator, smu_baud=smu_baud, smu_front_terminals=smu_front_terminals, smu_two_wire=smu_two_wire)

    if lia_address is not None:
      self._connect_lia(is_virt=lia_virt, visa_lib=visa_lib, lia_address=lia_address, lia_terminator=lia_terminator, lia_baud=lia_baud, lia_output_interface=lia_output_interface)

    if mono_address is not None:
      self._connect_monochromator(is_virt=mono_virt, visa_lib=visa_lib, mono_address=mono_address, mono_terminator=mono_terminator, mono_baud=mono_baud)

    if light_address is not None:
      self._connect_solarsim(is_virt=light_virt, light_address=light_address, light_recipe=light_recipe)

    if psu_address is not None:
      self._connect_psu(is_virt=psu_virt, visa_lib=visa_lib, psu_address=psu_address, psu_terminator=psu_terminator, psu_baud=psu_baud, psu_ocps=psu_ocps)

    if pcb_address is not None:
      self._connect_pcb(pcb_address=pcb_address, is_virt=pcb_virt)

    if motion_address is not None:
      self._connect_motion(motion_address=motion_address, is_virt=motion_virt)

  def disconnect_all_instruments(self):
    """Disconnect all instruments."""
    self.lg.debug("disconnecting instruments...")
    while len(self._connected_instruments) > 0:
      instr = self._connected_instruments.pop()
      self.lg.debug(instr)
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
      self.sm.outOn(on=False)

  def goto_pixel(self, pixel, mo):
    """Move to a pixel.

        Parameters
        ----------
        pixel : dict
            Pixel information dictionary.
        mo : class
            Motion object.

        Returns
        -------
        response : int
            Response code. 0 is good, everything else means fail.
        """
    if hasattr(self, "motion"):
      # ignore motion to position None and to places infinately far away
      # inf appears when the user wishes to disable motion for a specific pixel
      # in the layout configuration file
      there = pixel["pos"]
      if (there is not None) and (float("inf") not in there) and (float("-inf") not in there):
        mo.goto(there)
    return 0

  def select_pixel(self, mux_string, pcb):
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
    ret = None
    if mux_string is not None:
      resp = pcb.query("s")  # open all relays
      if resp == "":
        resp = pcb.query(mux_string)  # select the correct pixel
    else:
      resp = pcb.query("s")  # open all mux relays
    if resp == "":
      ret = 0
    return ret

  def set_experiment_relay(self, exp_relay, pcb):
    """Choose EQE or IV connection.

        Parameters
        ----------
        exp_relay : {"eqe", "iv"}
            Experiment name: either "eqe" or "iv" corresponding to relay.
        """
    ret = 0
    if hasattr(self, "pcb"):
      ret = None
      if pcb.query(exp_relay) == "":
        ret = 0
    return ret

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
      value = (unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii"))
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()

    return re.sub(r"[-\s]+", "-", value)

  def steady_state(self, t_dwell=10, NPLC=10, sourceVoltage=True, compliance=0.04, setPoint=0, senseRange="f", handler=lambda x: None):
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
    self.sm.setupDC(sourceVoltage=sourceVoltage, compliance=compliance, setPoint=setPoint, senseRange=senseRange)

    raw = self.sm.measureUntil(t_dwell=t_dwell, cb=handler)

    return raw

  def sweep(self, sourceVoltage=True, senseRange="f", compliance=0.04, nPoints=1001, stepDelay=0.005, start=1, end=0, NPLC=1, handler=lambda x: None):
    """Perform I-V measurement sweep.

        Make a series of measurements while sweeping the sourcemeter along linearly
        progressing voltage or current setpoints.
        """
    self.sm.setNPLC(NPLC)
    self.sm.setupSweep(sourceVoltage=sourceVoltage, compliance=compliance, stepDelay=stepDelay, nPoints=nPoints, start=start, end=end, senseRange=senseRange)
    handler(raw := self.sm.measure(nPoints))
    return raw

  def track_max_power(self, duration=30, NPLC=-1, extra="basic://7:10", handler=lambda x: None, voc_compliance=3, i_limit=0.04):
    """Track maximum power point.

        Parameters
        ----------
        duration : float or int
            Length of time to track max power for in seconds.
        NPLC : float or int
            Number of power line cycles. If -1, keep using previous setting.
        extra : str
            Extra protocol settings to pass to mppt.
        handler : handler object
            Handler with handle_data method to process data.
        """
    message = "Tracking maximum power point for {:} seconds".format(duration)

    raw = self.mppt.launch_tracker(duration=duration, NPLC=NPLC, extra=extra, callback=handler, voc_compliance=voc_compliance, i_limit=i_limit)
    self.mppt.reset()

    return raw

  def eqe(self, psu_ch1_voltage=0, psu_ch1_current=0, psu_ch2_voltage=0, psu_ch2_current=0, psu_ch3_voltage=0, psu_ch3_current=0, smu_voltage=0, smu_compliance=0.1, start_wl=350, end_wl=1100, num_points=76, grating_change_wls=None, filter_change_wls=None, time_constant=8, auto_gain=True, auto_gain_method="user", handler=lambda x: None):
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
    self.sm.setupDC(sourceVoltage=True, compliance=smu_compliance, setPoint=smu_voltage, senseRange="f")

    eqe_data = eqe.scan(self.lia, self.mono, self.psu, self.sm, psu_ch1_voltage, psu_ch1_current, psu_ch2_voltage, psu_ch2_current, psu_ch3_voltage, psu_ch3_current, smu_voltage, smu_compliance, start_wl, end_wl, num_points, grating_change_wls, filter_change_wls, time_constant, auto_gain, auto_gain_method, handler)

    return eqe_data

  def calibrate_psu(self, channel=1, max_current=0.5, current_steps=10, max_voltage=1):
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
    if hasattr(self, 'mono'):
      self.mono.filter = 5
      self.mono.wavelength = 300

    currents = np.linspace(0, max_current, int(current_steps), endpoint=True)

    # set smu to short circuit and enable output
    self.sm.setupDC(sourceVoltage=True, compliance=self.current_limit, setPoint=0, senseRange="a")

    # set up PSU
    self.psu.set_apply(channel=channel, voltage=max_voltage, current=0)
    self.psu.set_output_enable(True, channel)

    data = []
    for current in currents:
      self.psu.set_apply(channel=channel, voltage=max_voltage, current=current)
      time.sleep(1)
      psu_data = list(self.sm.measure()[0])
      psu_data.append(current)
      data.append(psu_data)

    # disable PSU
    self.psu.set_apply(channel=channel, voltage=max_voltage, current=0)
    self.psu.set_output_enable(False, channel)

    # disable smu
    self.sm.outOn(False)

    if hasattr(self, 'mono'):
      # unblock the monochromator
      self.mono.filter = 1
      self.mono.wavelength = 0

    return data


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
  with fabric(current_limit=0.1) as f:
    args = {}
    args['smu_address'] = "GPIB0::24::INSTR"
    args['smu_terminator'] = "\r"
    args['smu_baud'] = 57600
    f.connect_instruments(**args)
