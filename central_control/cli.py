#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# written by grey@christoforo.net

import central_control  # for __version__
from central_control.fabric import fabric
from central_control.handlers import (
    DataHandler,
    SettingsHandler,
    CacheHandler,
    StageHandler,
)

import argparse
import configparser
import distutils.util
import itertools
import json
import os
import pathlib
import types

from collections import deque

import appdirs
import numpy as np


# for updating prefrences
prefs = {}  # TODO: figure out how to un-global this


class cli:
    """The command line interface.

    Perform system actions using settings from the command line and a config file.
    """

    prefs_file = "preferences.ini"

    def __init__(self, appname="central-control"):
        """Construct object.

        Parameters
        ----------
        appname : str
            Application name for appdirs directory structuring.
        """
        # check and create application directories if needed
        self.dirs = appdirs.AppDirs(appname, appauthor=False)
        self.app_dir = pathlib.Path(self.dirs.user_data_dir)
        self.cache = pathlib.Path(self.dirs.user_cache_dir)
        self.log_dir = pathlib.Path(self.dirs.user_log_dir)
        if self.app_dir.exists() is False:
            self.app_dir.mkdir()
        if self.cache.exists() is False:
            self.cache.mkdir()
        if self.log_dir.exists() is False:
            self.log_dir.mkdir()

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context related to this object.

        Make sure everything gets cleaned up properly.
        """
        # TODO: add cleanup
        pass

    def _save_prefs(self):
        """Save argparse preferences to cache."""
        with open(self.cache.joinpath(self.prefs_file), "w") as f:
            json.dump(vars(self.args), f)

    def _load_prefs(self):
        """Load argparse preferences from cache.

        Returns
        -------
        args : types.SimpleNamespace
            Arguments loaded from file in a type that can accessed in the same way as
            an argparse namespace.    
        """
        with open(self.cache.joinpath(self.prefs_file), "r") as f:
            args = json.load(f)

        return types.SimpleNamespace(**args)

    def _load_config(self):
        """Find and load config file."""
        self.config = configparser.ConfigParser()
        cached_config_path = self.cache.joinpath("measurement_config.ini")
        if self.args.config_file is not None:
            # priority 1: CLI
            self.config.read(self.args.config_file)
        elif cached_config_path.exists() is True:
            # priority 2: cache dir
            self.config.read(cached_config_path)
        else:
            raise Exception(
                f"Config file path not found in CLI or at {cached_config_path}."
            )

    def _update_save_settings(self, folder, archive):
        """Tell saver MQTT client where to save and backup data.

        Parameters
        ----------
        folder : str
            Experiment folder name.
        archive : str
            Network address used to back up data files when an experiment completes.
        """
        # context manager handles disconnect
        with SettingsHandler() as sh:
            sh.connect(self.args.mqtt_host)
            # publish to data saver settings topic
            sh.start_q("data/saver")
            sh.update_settings(folder, archive)

    def _save_cache(self):
        """Send cached data to saver MQTT client."""
        with CacheHandler() as ch:
            ch.connect(self.args.mqtt_host)
            ch.start_q("data/cache")
            for file in self.cache.iterdir():
                with open(self.cache.joinpath(file), "r") as f:
                    contents = f.read()
                    ch.save_cache(file, contents)

    def _get_axes(self):
        """Look up number of stage axes from config file and init attribute."""
        self.stage_lengths = [int(x) for x in self.config["stage"]["length"].split(",")]
        # get number of stage axes
        self.axes = len(self.stage_lengths)

    def _get_stage_position(self):
        """Read the stage position and report it."""
        loc = []
        for axis in range(1, self.axes + 1, 1):
            loc.append(self.logic.controller.read_pos(axis))

        with StageHandler() as sh:
            sh.connect(self.args.mqtt_host)
            sh.start_q("data/stage")
            sh.handle_data(loc)

        return loc

    def _get_substrate_positions(self, experiment):
        """Calculate absolute positions of all substrate centres.

        Read in info from config file.

        Parameters
        ----------
        experiment : str
            Name used to look up the experiment centre stage position from the config
            file.

        Returns
        -------
        substrate_centres : list of lists
            Absolute substrate centre co-ordinates. Each sublist contains the positions
            along each axis.
        """
        experiment_centre = [
            int(x) for x in self.config["experiment_positions"][experiment].split(",")
        ]

        # read in number substrates in the array along each axis
        self.substrate_number = [
            int(x) for x in self.config["substrates"]["number"].split(",")
        ]

        # get number of substrate centres between the centre and the edge of the
        # substrate array along each axis, e.g. if there are 4 rows, there are 1.5
        # substrate centres to the outermost substrate
        substrate_offsets = []
        substrate_total = 1
        for number in self.substrate_number:
            if number % 2 == 0:
                offset = number / 2 - 0.5
            else:
                offset = np.floor(number / 2)
            substrate_offsets.append(offset)
            substrate_total = substrate_total * number

        self.substrate_total = substrate_total

        # read in substrate spacing in mm along each axis into a list
        substrate_spacing = [
            int(x) for x in self.config["substrates"]["spacing"].split(",")
        ]

        # read in step length in steps/mm
        self.steplength = float(self.config["stage"]["steplength"])

        # get absolute substrate centres along each axis
        axis_pos = []
        for offset, spacing, number, centre in zip(
            substrate_offsets,
            substrate_spacing,
            self.substrate_number,
            experiment_centre,
        ):
            abs_offset = offset * (spacing / self.steplength) + centre
            axis_pos.append(np.linspace(-abs_offset, abs_offset, number))

        # create array of positions
        substrate_centres = list(itertools.product(*axis_pos))

        return substrate_centres

    def _build_q(self, pixel_address_string, experiment):
        """Generate a queue of pixels we'll run through.

        Parameters
        ----------
        pixel_address_string : str
            Hexadecimal bitmask string.
        experiment : str
            Name used to look up the experiment centre stage position from the config
            file.

        Returns
        -------
        pixel_q : deque
        """
        # TODO: return support for inferring layout from pcb adapter resistors

        # get substrate centres
        substrate_centres = self._get_substrate_positions(experiment)

        # make sure as many layouts as labels were given
        if ((l1 := len(self.args.layouts)) != self.substrate_total) or (
            (l2 := len(self.args.labels)) != self.substrate_total
        ):
            raise ValueError(
                f"Lists of layouts and labels must match number of substrates in the array: {self.substrate_total}. Layouts list has length {l1} and labels list has length {l2}."
            )

        # create a substrate queue where each element is a dictionary of info about the
        # layout from the config file
        substrate_q = []
        i = 0
        for layout, label, centre in zip(
            self.args.layouts, self.args.labels, substrate_centres
        ):
            # get pcb adapter info from config file
            pcb_name = self.config[layout]["pcb_name"]

            # read in pixel positions from layout in config file
            config_pos = [int(x) for x in self.config[layout]["positions"].split(",")]
            pixel_positions = []
            for i in range(0, len(config_pos), self.axes):
                abs_pixel_position = [
                    int(x + y) for x, y in zip(config_pos[i : i + self.axes], centre)
                ]
                pixel_positions.append(abs_pixel_position)

            # find co-ordinate of substrate in the array
            _substrates = np.linspace(1, self.substrate_total, self.substrate_total)
            _array = np.reshape(_substrates, self.substrate_number)
            array_loc = [int(ix) + 1 for ix in np.where(_array == i)]

            substrate_dict = {
                "label": label,
                "array_loc": array_loc,
                "layout": layout,
                "pcb_name": pcb_name,
                "pcb_contact_pads": self.config[pcb_name]["pcb_contact_pads"],
                "pcb_resistor": self.config[pcb_name]["pcb_resistor"],
                "pixels": self.config[layout]["pixels"].split(","),
                "pixel_positions": pixel_positions,
                "areas": self.config[layout]["areas"].split(","),
            }
            substrate_q.append(substrate_dict)

            i += 1

        # TODO: return support for pixel strings that aren't hex bitmasks
        # convert hex bitmask string into bit list where 1's and 0's represent whether
        # a pixel should be measured or not, respectively
        bitmask = [int(x) for x in bin(int(pixel_address_string, 16))[2:]]

        # build pixel queue
        pixel_q = deque()
        for substrate in substrate_q:
            # git bitmask for the substrate pcb
            sub_bitmask = [
                bitmask.pop(-1) for i in range(substrate["pcb_contact_pads"])
            ].reverse()
            # select pixels to measure from layout
            for pixel in substrate["pixels"]:
                if sub_bitmask[pixel - 1] == 1:
                    pixel_dict = {
                        "label": substrate["label"],
                        "layout": substrate["layout"],
                        "array_loc": substrate["array_loc"],
                        "pixel": pixel,
                        "position": substrate["pixel_positions"][pixel - 1],
                        "area": substrate["areas"][pixel - 1],
                    }
                    pixel_q.append(pixel_dict)

        return pixel_q

    def _connect_instruments(self):
        """Init fabric object and connect instruments.

        Determine which instruments are connected and their settings from the config
        file.
        """
        if self.args.dummy is False:
            visa_lib = self.config["visa"]["visa_lib"]
            smu_address = self.config["smu"]["address"]
            smu_terminator = self.config["smu"]["terminator"]
            smu_baud = self.config["smu"]["baud"]
            light_address = self.config["solarsim"]["address"]
            controller_address = self.config["controller"]["address"]
            lia_address = self.config["lia"]["address"]
            lia_output_interface = self.config["lia"]["output_interface"]
            mono_address = self.config["mono"]["address"]
            psu_address = self.config["psu"]["address"]
        else:
            visa_lib = None
            smu_address = None
            smu_terminator = None
            smu_baud = None
            light_address = None
            controller_address = None
            lia_address = None
            lia_output_interface = None
            mono_address = None
            psu_address = None

        # connect to insturments
        self.logic.connect(
            dummy=self.args.dummy,
            visa_lib=visa_lib,
            smu_address=smu_address,
            smu_terminator=smu_terminator,
            smu_baud=smu_baud,
            light_address=light_address,
            controller_address=controller_address,
            lia_address=lia_address,
            lia_output_interface=lia_output_interface,
            mono_address=mono_address,
            psu_address=psu_address,
        )

        # set up smu terminals
        self.logic.sm.setTerminals(
            front=self.config.getboolean("smu", "front_terminals")
        )
        self.logic.sm.setWires(twoWire=self.config.getboolean("smu", "two_wire"))

    def _set_experiment_relay(self, experiment):
        """Set the experiment relay configuration.

        Parameters
        ----------
        experiment : str
            Experiment being performed.
        """
        if experiment == "solarsim":
            # TODO: add relay config func
            pass
        elif experiment == "eqe":
            # TODO: add relay config func
            pass
        else:
            raise ValueError(f"Experiment '{experiment}' not supported.")

    def _ivt(self):
        """Run through pixel queue of i-v-t measurements."""
        self._set_experiment_relay("solarsim")

        # create mqtt data handlers for i-v-t measurements
        # mqtt publisher topics for each handler
        subtopics = []
        subtopics.append(f"data/vt")
        subtopics.append(f"data/iv")
        subtopics.append(f"data/mppt")
        subtopics.append(f"data/it")

        # instantiate handlers
        vdh = DataHandler()
        ivdh = DataHandler()
        mdh = DataHandler()
        cdh = DataHandler()
        handlers = [vdh, ivdh, mdh, cdh]

        # connect handlers to broker and start publisher threads
        for i, dh in enumerate(handlers):
            dh.connect(self.args.mqtt_host)
            dh.start_q(subtopics[i])

        last_label = None
        # scan through the pixels and do the requested measurements
        while len(self.iv_pixel_queue) > 0:
            pixel = self.iv_pixel_queue.popleft()
            label = pixel["label"]
            pix = pixel["pixel"]
            print(f"\nOperating on substrate {label}, pixel {pix}...")

            # add id str to handlers to display on plots
            for dh in handlers:
                dh.idn = f"{label}_pixel{pix}"

            # we have a new substrate
            if last_label != label:
                print(f"New substrate using '{pixel['layout']}' layout!")
                last_label = label

            # move to pixel
            for i, ax_pos in enumerate(pixel["position"]):
                self.logic.controller.goto(i + 1, ax_pos)

            # init parameters derived from steadystate measurements
            ssvoc = None
            ssisc = None

            # get or estimate compliance current
            if type(self.args.current_compliance_override) == float:
                compliance_i = self.args.current_compliance_override
            else:
                # estimate compliance current based on area
                compliance_i = self.logic.compliance_current_guess(pixel["area"])

            # steady state v@constant I measured here - usually Voc
            if self.args.v_t > 0:
                # clear v@constant I plot
                vdh.clear()

                vt = self.logic.steadyState(
                    t_dwell=self.args.v_t,
                    NPLC=self.args.steadystate_nplc,
                    stepDelay=self.args.steadystate_step_delay,
                    sourceVoltage=False,
                    compliance=self.args.voltage_compliance_override,
                    senseRange="a",
                    setPoint=self.args.steadystate_i,
                    handler=vdh,
                )

                # if this was at Voc, use the last measurement as estimate of Voc
                if self.args.steadystate_i == 0:
                    ssvoc = vt[-1]
                    self.logic.mppt.Voc = ssvoc

            # TODO: add support for dark measurement, has to use autorange
            if self.args.sweep_1 is True:
                # determine sweep start voltage
                if type(self.args.scan_start_override_1) == float:
                    start = self.args.scan_start_override_1
                elif ssvoc is not None:
                    start = ssvoc * (
                        1 + (self.config.getfloat("iv", "percent_beyond_voc") / 100)
                    )
                else:
                    raise ValueError(
                        f"Start voltage wasn't given and couldn't be inferred."
                    )

                # determine sweep end voltage
                if type(self.args.scan_end_override_1) == float:
                    end = self.args.scan_end_override_1
                else:
                    end = (
                        -1
                        * np.sign(ssvoc)
                        * self.config.getfloat("iv", "voltage_beyond_isc")
                    )

                print(f"Sweeping voltage from {start} V to {end} V")

                # clear iv plot
                ivdh.clear()
                iv1 = self.logic.sweep(
                    sourceVoltage=True,
                    compliance=compliance_i,
                    senseRange="f",
                    nPoints=self.args.scan_points,
                    stepDelay=self.args.scan_step_delay,
                    start=start,
                    end=end,
                    NPLC=self.args.scan_nplc,
                    handler=ivdh,
                )

                Pmax_sweep1, Vmpp1, Impp1, maxIx1 = self.logic.mppt.which_max_power(iv1)

            if self.args.sweep_2 is True:
                # sweep the opposite way to sweep 1
                start = end
                end = start

                print(f"Sweeping voltage from {start} V to {end} V")

                iv2 = self.logic.sweep(
                    sourceVoltage=True,
                    senseRange="f",
                    compliance=compliance_i,
                    nPoints=self.args.scan_points,
                    start=start,
                    end=end,
                    NPLC=self.args.scan_nplc,
                    handler=ivdh,
                )

                Pmax_sweep2, Vmpp2, Impp2, maxIx2 = self.logic.mppt.which_max_power(iv2)

            # determine Vmpp and current compliance for mppt
            if (self.args.sweep_1 is True) & (self.args.sweep_2 is True):
                if abs(Pmax_sweep1) > abs(Pmax_sweep2):
                    Vmpp = Vmpp1
                    compliance_i = Impp1 * 5
                else:
                    Vmpp = Vmpp2
                    compliance_i = Impp2 * 5
            elif self.args.sweep_1 is True:
                Vmpp = Vmpp1
                compliance_i = Impp1 * 5
            else:
                # no sweeps have been measured so max power tracker will estimate Vmpp
                # based on Voc (or measure it if also no Voc) and will use initial
                # compliance set before any measurements were taken.
                Vmpp = None
            self.logic.mppt.Vmpp = Vmpp
            self.logic.mppt.current_compliance = compliance_i

            if self.args.mppt_t > 0:
                print(f"Tracking maximum power point for {self.args.mppt_t} seconds")

                # clear mppt plot
                mdh.clear()
                self.logic.track_max_power(
                    self.args.mppt_t,
                    NPLC=self.args.steadystate_nplc,
                    stepDelay=self.args.steadystate_step_delay,
                    extra=self.args.mppt_params,
                    handler=mdh,
                )

            if self.args.i_t > 0:
                # steady state I@constant V measured here - usually Isc
                # clear I@constant V plot
                cdh.clear()
                it = self.logic.steadyState(
                    t_dwell=self.args.i_t,
                    NPLC=self.args.steadystate_nplc,
                    stepDelay=self.args.steadystate_step_delay,
                    sourceVoltage=True,
                    compliance=compliance_i,
                    senseRange="a",
                    setPoint=self.args.steadystate_v,
                    handler=cdh,
                )

        # clean up mqtt publishers
        for dh in handlers:
            dh.end_q()
            dh.disconnect()

        self.logic.runDone()

    def _eqe(self):
        """Run through pixel queue of EQE measurements."""
        self._set_experiment_relay("eqe")

        # create mqtt data handler for eqe
        edh = DataHandler()
        edh.connect(self.args.mqtt_host)
        edh.start_q("data/eqe")

        while len(self.eqe_pixel_queue) > 0:
            pixel = self.eqe_pixel_queue.popleft()
            label = pixel["label"]
            pix = pixel["pixel"]
            print(f"\nOperating on substrate {label}, pixel {pix}...")

            # add id str to handlers to display on plots
            edh.idn = f"{label}_pixel{pix}"

            # we have a new substrate
            if last_label != label:
                print(f"New substrate using '{pixel['layout']}' layout!")
                last_label = label

            # move to pixel
            for i, ax_pos in enumerate(pixel["position"]):
                self.logic.controller.goto(i + 1, ax_pos)

            print(
                f"Scanning EQE from {self.args.eqe_start_wl} nm to {self.args.eqe_end_wl} nm"
            )

            # clear eqe plot
            edh.clear()
            self.logic.eqe(
                psu_ch1_voltage=self.config.getfloat("psu", "ch1_voltage"),
                psu_ch1_current=self.args.psu_is[0],
                psu_ch2_voltage=self.config.getfloat("psu", "ch2_voltage"),
                psu_ch2_current=self.args.psu_is[1],
                psu_ch3_voltage=self.config.getfloat("psu", "ch3_voltage"),
                psu_ch3_current=self.args.psu_is[2],
                smu_voltage=self.args.eqe_smu_v,
                calibration=self.args.calibrate_eqe,
                ref_measurement_path=self.args.eqe_ref_meas_path,
                ref_measurement_file_header=self.args.eqe_ref_meas_header_len,
                ref_eqe_path=self.args.eqe_ref_cal_path,
                ref_spectrum_path=self.args.eqe_ref_spec_path,
                start_wl=self.args.eqe_start_wl,
                end_wl=self.args.eqe_end_wl,
                num_points=self.args.eqe_num_wls,
                repeats=self.args.eqe_repeats,
                grating_change_wls=self.args.eqe_grating_change_wls,
                filter_change_wls=self.args.eqe_filter_change_wls,
                auto_gain=not (self.args.eqe_autogain_off),
                auto_gain_method=self.args.eqe_autogain_method,
                integration_time=self.args.eqe_integration_time,
                handler=edh,
            )

        # clean up mqtt publishers
        edh.end_q()
        edh.disconnect()

    def run(self):
        """Act on command line instructions."""
        # get arguments parsed to the command line
        self.args = self._get_args()

        if self.args.repeat is True:
            # retreive args from cached preferences
            self.args = self._load_prefs()
        else:
            # save argparse prefs to cache
            self._save_prefs()

        # find and load config file
        self._load_config()

        # look up number of stage axes from config and init attribute
        self._get_axes()

        # create the control logic entity and connect instruments
        self.logic = fabric()
        self._connect_instruments()

        # tell mqtt data saver where to save
        self._update_save_settings(
            self.args.destination, self.config["network"]["archive"]
        )

        # home the stage
        if self.args.home is True:
            for i in range(self.axes):
                # axes are 1-indexed
                self.logic.controller.home(i + 1)

        # goto stage position
        if self.args.goto is not None:
            for i, pos in enumerate(self.args.goto):
                self.logic.controller.goto(i + 1, pos)

        # read stage position
        if self.args.read_stage is True:
            self._get_stage_position()

        # calibrate LED PSU if required
        if self.args.calibrate_psu is True:
            pdh = DataHandler()
            pdh.connect(self.args.mqtt_host)
            pdh.start_q("data/psu")
            pdh.idn = "psu_calibration"
            self._set_experiment_relay("eqe")
            self.logic.calibrate_psu(
                self.args.calibrate_psu_ch, loc=self.args.position_override, handler=pdh
            )
            pdh.end_q()
            pdh.disconnect()

        # measure EQE calibration diode if required
        if self.args.calibrate_eqe is True:
            self._set_experiment_relay("eqe")
            # TODO: add calibrate EQE func

        # perform solar sim calibration measurement
        if self.calibrate_solarsim is True:
            # TODO; add calibrate solar sim func
            self._set_experiment_relay("solarsim")

            if self.args.wavelabs_spec_cal_path != "":
                spectrum_cal = np.genfromtxt(
                    self.args.wavelabs_spec_cal_path, skip_header=1, delimiter="\t"
                )[:, 1]
            else:
                spectrum_cal = None

            # save spectrum
            if self.logic.spectrum is not None:
                sdh = DataHandler()
                sdh.connect(self.args.mqtt_host)
                sdh.start_q("data/spectrum")
                sdh.idn = "spectrum"
                sdh.handle_data(self.logic.spectrum)
                sdh.end_q()
                sdh.disconnect()

        # calibration data is saved to cache so copy those files to save directory now
        self._save_cache()

        # build up the queue of pixels to run through
        if self.args.dummy is True:
            self.args.iv_pixel_address = "0x1"
            self.args.eqe_pixel_address = "0x1"

        if self.args.iv_pixel_address is not None:
            self.iv_pixel_queue = self._build_q(
                self.args.iv_pixel_address, experiment="solarsim"
            )
        else:
            self.iv_pixel_queue = []

        if self.args.eqe_pixel_address is not None:
            self.eqe_pixel_queue = self._build_q(
                self.args.eqe_pixel_address, experiment="eqe"
            )
        else:
            self.eqe_pixel_queue = []

        # measure i-v-t
        if (
            self.args.v_t
            or self.args.i_t
            or self.args.sweep_1
            or self.args.sweep_2
            or self.args.mppt_t > 0
        ) & len(self.iv_pixel_queue) > 0:
            self._ivt()

        # measure eqe
        if (self.args.eqe is True) & (len(self.eqe_pixel_queue) > 0):
            self._eqe()

    class FullPaths(argparse.Action):
        """Expand user- and relative-paths and save pref arg parse action."""

        def __call__(self, parser, namespace, values, option_string=None):
            value = os.path.abspath(os.path.expanduser(values))
            setattr(namespace, self.dest, value)
            prefs[self.dest] = value

    class RecordPref(argparse.Action):
        """Save pref arg parse action."""

        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)
            if values is not None:  # don't save None params to prefs
                prefs[self.dest] = values

    def is_dir(self, dirname):
        """Checks if a path is an actual directory"""
        if (not os.path.isdir(dirname)) and dirname != "__tmp__":
            msg = "{0} is not a directory".format(dirname)
            raise argparse.ArgumentTypeError(msg)
        else:
            return dirname

    def str2bool(self, v):
        """Convert str to bool."""
        return bool(distutils.util.strtobool(v))

    def _get_args(self):
        """Get CLI arguments and options."""
        parser = argparse.ArgumentParser(
            description="Automated solar cell IV curve collector using a Keithley 24XX sourcemeter. Data is written to HDF5 files and human readable messages are written to stdout. * denotes arguments that are remembered between calls."
        )

        parser.add_argument(
            "--repeat",
            action="store_true",
            help="Repeat the last user-defined run action.",
        )
        parser.add_argument(
            "-m", "--mqtt-mode", action="store_true", help="Run as an MQTT client",
        )
        parser.add_argument(
            "-c",
            "--config-file",
            action=self.FullPaths,
            help="Path to configuration file",
        )
        parser.add_argument(
            "-v",
            "--version",
            action="version",
            version="%(prog)s " + central_control.__version__,
        )
        parser.add_argument(
            "-o", "--operator", type=str, required=True, help="Name of operator"
        )
        parser.add_argument(
            "-r",
            "--run-description",
            type=str,
            required=True,
            help="Words describing the measurements about to be taken",
        )

        measure = parser.add_argument_group(
            "optional arguments for measurement configuration"
        )
        measure.add_argument(
            "-d",
            "--destination",
            help="*Directory in which to save the output data, '__tmp__' will use a system default temporary directory",
            type=self.is_dir,
            action=self.FullPaths,
        )
        measure.add_argument(
            "-a",
            "--iv-pixel-address",
            default=None,
            type=str,
            help='Hexadecimal bit mask for enabled pixels for I-V-t measurements. Also takes letter-number pixel addresses "0xFC == A1A2A3A4A5A6"',
        )
        measure.add_argument(
            "-b",
            "--eqe-pixel-address",
            default=None,
            type=str,
            help='Hexadecimal bit mask for enabled pixels for EQE measurements. Also takes letter-number pixel addresses "0xFC == A1A2A3A4A5A6"',
        )
        measure.add_argument(
            "-i",
            "--layouts",
            type=int,
            nargs="*",
            help="*List of substrate layout names to use for finding pixel information from the configuration file",
        )
        measure.add_argument(
            "--labels", nargs="*", help="*List of Substrate labels",
        )
        measure.add_argument(
            "--mqtt-host",
            type=str,
            action=self.RecordPref,
            default="",
            help="*IP address or hostname of mqtt broker",
        )
        measure.add_argument(
            "--sweep-1",
            type=self.str2bool,
            default=True,
            action=self.RecordPref,
            const=True,
            help="*Do an I-V sweep from Voc --> Isc",
        )
        measure.add_argument(
            "--sweep-2",
            type=self.str2bool,
            default=True,
            action=self.RecordPref,
            const=True,
            help="*Do an I-V sweep from Isc --> Voc",
        )
        measure.add_argument(
            "--steadystate-v",
            type=float,
            action=self.RecordPref,
            default=0,
            help="*Steady state value of V to measure I",
        )
        measure.add_argument(
            "--steadystate-i",
            type=float,
            action=self.RecordPref,
            default=0,
            help="*Steady state value of I to measure V",
        )
        measure.add_argument(
            "--i-t",
            type=float,
            action=self.RecordPref,
            default=10.0,
            help="*Number of seconds to measure to find steady state I@constant V",
        )
        measure.add_argument(
            "--v-t",
            type=float,
            action=self.RecordPref,
            default=10.0,
            help="*Number of seconds to measure to find steady state V@constant I",
        )
        measure.add_argument(
            "--mppt-t",
            type=float,
            action=self.RecordPref,
            default=37.0,
            help="*Do maximum power point tracking for this many seconds",
        )
        measure.add_argument(
            "--mppt-params",
            type=str,
            action=self.RecordPref,
            default="basic://7:10",
            help="*Extra configuration parameters for the maximum power point tracker, see https://git.io/fjfrZ",
        )
        measure.add_argument(
            "--eqe",
            type=self.str2bool,
            default=True,
            action=self.RecordPref,
            const=True,
            help="*Do an EQE scan",
        )

        setup = parser.add_argument_group("optional arguments for setup configuration")
        setup.add_argument(
            "--ignore-adapter-resistors",
            type=self.str2bool,
            default=True,
            action=self.RecordPref,
            const=True,
            help="*Don't consider the resistor value of adapter boards when determining device layouts",
        )
        setup.add_argument(
            "--light-recipe",
            type=str,
            action=self.RecordPref,
            default="AM1.5_1.0SUN",
            help="Recipe name for Wavelabs to load",
        )
        setup.add_argument(
            "--wavelabs-spec-cal-path",
            type=str,
            action=self.RecordPref,
            default="",
            help="Path to Wavelabs spectrum calibration file",
        )
        setup.add_argument(
            "--rear",
            type=self.str2bool,
            default=True,
            action=self.RecordPref,
            help="*Use the rear terminals",
        )
        setup.add_argument(
            "--four-wire",
            type=self.str2bool,
            default=True,
            action=self.RecordPref,
            help="*Use four wire mode (the default)",
        )
        setup.add_argument(
            "--voltage-compliance-override",
            default=2,
            type=float,
            help="Override voltage complaince setting used during Voc measurement",
        )
        setup.add_argument(
            "--current-compliance-override",
            type=float,
            help="Override current compliance value used during I-V scans",
        )
        setup.add_argument(
            "--scan-start-override-1",
            type=float,
            help="Override the start sweep voltage limit for sweep-1",
        )
        setup.add_argument(
            "--scan-end-override-1",
            type=float,
            help="Override the end sweep voltage limit for sweep-1",
        )
        setup.add_argument(
            "--scan-start-override-2",
            type=float,
            help="Override the start sweep voltage limit for sweep-2",
        )
        setup.add_argument(
            "--scan-end-override-2",
            type=float,
            help="Override the end sweep voltage limit for sweep-2",
        )
        setup.add_argument(
            "--scan-points",
            type=int,
            action=self.RecordPref,
            default=101,
            help="*Number of measurement points in I-V curve",
        )
        setup.add_argument(
            "--scan-nplc",
            type=float,
            action=self.RecordPref,
            default=1,
            help="*Sourcemeter NPLC setting to use during I-V scans",
        )
        setup.add_argument(
            "--steadystate-nplc",
            type=float,
            action=self.RecordPref,
            default=1,
            help="*Sourcemeter NPLC setting to use during steady-state scans and max power point tracking",
        )
        setup.add_argument(
            "--scan-step-delay",
            type=float,
            action=self.RecordPref,
            default=-1,
            help="*Sourcemeter settling delay in seconds to use during I-V scans. -1 = auto",
        )
        setup.add_argument(
            "--steadystate-step-delay",
            type=float,
            action=self.RecordPref,
            default=-1,
            help="*Sourcemeter settling delay in seconds to use during steady-state scans and max power point tracking. -1 = auto",
        )
        setup.add_argument(
            "--sm-terminator",
            type=str,
            action=self.RecordPref,
            default="0A",
            help="*Visa comms read & write terminator (enter in hex)",
        )
        setup.add_argument(
            "--sm-baud",
            type=int,
            action=self.RecordPref,
            default=57600,
            help="*Visa serial comms baud rate",
        )
        setup.add_argument(
            "--home",
            default=False,
            action="store_true",
            help="Home the stage along each axis",
        )
        setup.add_argument(
            "--read-stage",
            default=False,
            action="store_true",
            help="Read the current stage position",
        )
        setup.add_argument(
            "--goto",
            type=float,
            nargs="+",
            default=None,
            help="Go to stage position. Input is a list of positions in steps along each available axis in order",
        )
        setup.add_argument(
            "--calibrate-diodes",
            default=False,
            action="store_true",
            help="Read diode ADC counts now and store those as corresponding to 1.0 sun intensity",
        )
        setup.add_argument(
            "--diode-calibration-values",
            type=int,
            nargs=2,
            action=self.RecordPref,
            default=(1, 1),
            help="*Calibration ADC counts for diodes D1 and D2 that correspond to 1.0 sun intensity",
        )
        setup.add_argument(
            "--ignore-diodes",
            default=False,
            action="store_true",
            help="Ignore intensity diode readings and assume 1.0 sun illumination",
        )
        setup.add_argument(
            "--psu-vs",
            type=float,
            action=self.RecordPref,
            nargs=3,
            default=[0, 0, 0],
            help="*LED PSU channel voltages (V)",
        )
        setup.add_argument(
            "--psu-is",
            type=float,
            action=self.RecordPref,
            nargs=3,
            default=[0, 0, 0],
            help="*LED PSU channel currents (A)",
        )
        setup.add_argument(
            "--eqe-integration-time",
            type=int,
            action=self.RecordPref,
            default=8,
            help="*Lock-in amplifier integration time setting (integer corresponding to a time)",
        )
        setup.add_argument(
            "--eqe-smu-v",
            type=float,
            action=self.RecordPref,
            default=0,
            help="*Sourcemeter bias voltage during EQE scan",
        )
        setup.add_argument(
            "--calibrate-eqe",
            action="store_true",
            help="Measure spectral response of reference photodiode",
        )
        setup.add_argument(
            "--eqe-ref-meas-path",
            type=str,
            action=self.RecordPref,
            help="Path to EQE reference photodiode measurement data",
        )
        setup.add_argument(
            "--eqe-ref-meas-header_len",
            type=int,
            action=self.RecordPref,
            default=1,
            help="Number of header rows in EQE ref photodiode measurement data file",
        )
        setup.add_argument(
            "--eqe-ref-cal-path",
            type=str,
            action=self.RecordPref,
            help="Path to EQE reference photodiode calibrated data",
        )
        setup.add_argument(
            "--eqe-ref-spec-path",
            type=str,
            action=self.RecordPref,
            help="Path to reference spectrum for integrated Jsc calculation",
        )
        setup.add_argument(
            "--eqe-start-wl",
            type=float,
            action=self.RecordPref,
            default=350,
            help="Starting wavelength for EQE scan in nm",
        )
        setup.add_argument(
            "--eqe-end-wl",
            type=float,
            action=self.RecordPref,
            default=1100,
            help="End wavelength for EQE scan in nm",
        )
        setup.add_argument(
            "--eqe-num-wls",
            type=float,
            action=self.RecordPref,
            default=76,
            help="Number of wavelegnths to measure in EQE scan",
        )
        setup.add_argument(
            "--eqe-repeats",
            type=int,
            action=self.RecordPref,
            default=1,
            help="Number of repeat measurements at each wavelength",
        )
        setup.add_argument(
            "--eqe-grating-change-wls",
            type=float,
            nargs="+",
            default=None,
            help="Wavelengths in nm at which to change gratings",
        )
        setup.add_argument(
            "--eqe-filter-change-wls",
            type=float,
            nargs="+",
            default=None,
            help="Wavelengths in nm at which to change filters",
        )
        setup.add_argument(
            "--eqe-autogain-off",
            action="store_true",
            help="Disable automatic gain setting",
        )
        setup.add_argument(
            "--eqe-autogain-method",
            type=str,
            default="user",
            action=self.RecordPref,
            help="Method of automatically establishing gain setting",
        )
        setup.add_argument(
            "--calibrate-psu",
            action="store_true",
            help="Calibrate PSU current to LEDs measuring reference photodiode",
        )
        setup.add_argument(
            "--calibrate-psu-ch",
            type=int,
            action=self.RecordPref,
            default=1,
            help="PSU channel to calibrate: 1, 2, or 3",
        )
        setup.add_argument(
            "--position-override",
            type=float,
            nargs="+",
            default=None,
            help="Override position given by pixel selection and use this list of coordinates (position along each axis) instead",
        )

        testing = parser.add_argument_group("optional arguments for debugging/testing")
        testing.add_argument(
            "--dummy",
            default=False,
            action="store_true",
            help="Run in dummy mode (doesn't need sourcemeter, generates simulated device data)",
        )
        testing.add_argument(
            "--scan",
            default=False,
            action="store_true",
            help="Scan for obvious VISA resource names, print them and exit",
        )
        testing.add_argument(
            "--test-hardware",
            default=False,
            action="store_true",
            help="Exercises all the hardware, used to check for and debug issues",
        )

        return parser.parse_args()


if __name__ == "__main__":
    with cli() as c:
        c.run()

