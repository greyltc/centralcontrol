#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# written by grey@christoforo.net

import central_control  # for __version__
from central_control.fabric import fabric
from central_control.handlers import (
    DataHandler,
    SettingsHandler,
)

import argparse
import configparser
import distutils.util
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

    def _format_args(self):
        """Re-format argparse arguments as needed."""
        self.args.sm_terminator = bytearray.fromhex(self.args.sm_terminator).decode()
        if self.args.light_address.upper() == "NONE":
            self.args.light_address = None
        if self.args.motion_address.upper() == "NONE":
            self.args.motion_address = None

    def run(self):
        """Act on command line instructions."""
        # get arguments parsed to the command line
        self.args = self.get_args()

        if self.args.repeat is True:
            # retreive args from cached preferences
            self.args = self._load_prefs()
        else:
            # save argparse prefs to cache
            self._save_prefs()

        # find and load config file
        self._load_config()

        # re-format argparse arguments as needed
        self._format_args()

        # create the control logic entity
        self.logic = fabric()

        # tell save client where to save data
        settings_handler = SettingsHandler()
        settings_handler.connect(self.args.mqtt_host)
        settings_handler.start_q("data/saver")
        settings_handler.update_settings(
            self.args.destination, self.config["network"]["archive"]
        )

        # connect to PCB and sourcemeter
        self.logic.connect(
            dummy=self.args.dummy,
            visa_lib=self.args.visa_lib,
            smu_address=self.args.sm_address,
            smu_terminator=self.args.sm_terminator,
            smu_baud=self.args.sm_baud,
            light_address=self.args.light_address,
            stage_address=self.args.motion_address,
            mux_address=self.args.pcb_address,
            lia_address=self.args.lia_address,
            mono_address=self.args.mono_address,
            psu_address=self.args.psu_address,
            lia_output_interface=self.args.lia_output_interface,
            ignore_adapter_resistors=self.args.ignore_adapter_resistors,
        )

        if self.args.dummy:
            self.args.iv_pixel_address = "A1"
            self.args.eqe_pixel_address = "A1"
        else:
            if self.args.rear == False:
                self.logic.sm.setTerminals(front=True)
            if self.args.four_wire == False:
                self.logic.sm.setWires(twoWire=True)

        # build up the queue of pixels to run through
        if self.args.iv_pixel_address is not None:
            iv_pixel_queue = self.buildQ(self.args.iv_pixel_address)
        else:
            iv_pixel_queue = []

        if self.args.eqe_pixel_address is not None:
            eqe_pixel_queue = self.buildQ(self.args.eqe_pixel_address)
        else:
            eqe_pixel_queue = []

        # either test hardware, calibrate LED PSU, or scan devices
        if self.args.test_hardware is True:
            if (iv_pixel_queue == []) & (eqe_pixel_queue == []):
                holders_to_test = self.logic.pcb.substratesConnected
            else:
                # turn the address que into a string of substrates
                mash = ""
                for pix in set(iv_pixel_queue + eqe_pixel_queue):
                    mash = mash + pix[0][0]
                # delete the numbers
                # mash = mash.translate({48:None,49:None,50:None,51:None,52:None,53:None,54:None,55:None,56:None})
                holders_to_test = "".join(sorted(set(mash)))  # remove dupes
            self.logic.hardwareTest(holders_to_test.upper())
        elif self.args.calibrate_psu is True:
            pdh = DataHandler()
            pdh.connect(self.args.mqtt_host)
            pdh.start_q("data/psu")
            pdh.idn = "psu_calibration"
            self.logic.calibrate_psu(
                self.args.calibrate_psu_ch, loc=self.args.position_override, handler=pdh
            )
            pdh.end_q()
            pdh.disconnect()
        else:
            #  do run setup things now like diode calibration and opening the data storage file
            if self.args.calibrate_diodes == True:
                diode_cal = True
            else:
                diode_cal = self.args.diode_calibration_values

            if self.args.wavelabs_spec_cal_path != "":
                spectrum_cal = np.genfromtxt(
                    self.args.wavelabs_spec_cal_path, skip_header=1, delimiter="\t"
                )[:, 1]
            else:
                spectrum_cal = None

            intensity = self.logic.runSetup(
                self.args.operator,
                diode_cal,
                ignore_diodes=self.args.ignore_diodes,
                run_description=self.args.run_description,
                recipe=self.args.light_recipe,
                spectrum_cal=spectrum_cal,
            )

            # save spectrum
            if self.logic.spectrum is not None:
                sdh = DataHandler()
                sdh.connect(self.args.mqtt_host)
                sdh.start_q("data/spectrum")
                sdh.idn = "spectrum"
                sdh.handle_data(self.logic.spectrum)
                sdh.end_q()
                sdh.disconnect()

            # record all arguments into the run file
            self.logic.f.create_group("args")
            for attr, value in self.args.__dict__.items():
                self.logic.f["args"].attrs[attr] = str(value)

            if self.args.calibrate_diodes == True:
                d1_cal = intensity["diode_1_adc"]
                d2_cal = intensity["diode_2_adc"]
                print(
                    "Setting present intensity diode readings to be used as future 1.0 sun refrence values: [{:}, {:}]".format(
                        d1_cal, d2_cal
                    )
                )
                # save the newly read diode calibraion values to the prefs file
                config = configparser.ConfigParser()
                config.read(self.config_file_fullpath)
                config[self.config_section]["diode_calibration_values"] = str(
                    [d1_cal, d2_cal]
                )
                with open(self.config_file_fullpath, "w") as configfile:
                    config.write(configfile)

            if (
                self.args.v_t
                or self.args.i_t
                or self.args.sweep_1
                or self.args.sweep_2
                or self.args.mppt_t
                or self.args.eqe > 0
            ):
                # create mqtt data handlers
                if self.args.mqtt_host != "":
                    # mqtt publisher topics for each handler
                    subtopics = []
                    subtopics.append(f"data/vt")
                    subtopics.append(f"data/iv")
                    subtopics.append(f"data/mppt")
                    subtopics.append(f"data/it")
                    subtopics.append(f"data/eqe")

                    # instantiate handlers
                    vdh = DataHandler()
                    ivdh = DataHandler()
                    mdh = DataHandler()
                    cdh = DataHandler()
                    edh = DataHandler()
                    handlers = [vdh, ivdh, mdh, cdh, edh]

                    # connect handlers to broker and start publisher threads
                    for i, dh in enumerate(handlers):
                        dh.connect(self.args.mqtt_host)
                        dh.start_q(subtopics[i])

                last_substrate = None
                # scan through the pixels and do the requested measurements
                for pixel in iv_pixel_queue:
                    substrate = pixel[0][0].upper()
                    pix = pixel[0][1]
                    print(
                        "\nOperating on substrate {:s}, pixel {:s}...".format(
                            substrate, pix
                        )
                    )
                    # add id str to handlers to display on plots
                    for dh in handlers:
                        dh.idn = f"substrate{substrate}_pixel{pix}"

                    if last_substrate != substrate:  # we have a new substrate
                        print('New substrate using "{:}" layout!'.format(pixel[3]))
                        last_substrate = substrate

                        substrate_ready = self.logic.substrateSetup(
                            position=substrate, layout_name=pixel[3],
                        )

                    pixel_ready = self.logic.pixelSetup(pixel)
                    if pixel_ready and substrate_ready:

                        if self.args.v_t > 0:
                            # steady state v@constant I measured here - usually Voc
                            # clear v@constant I plot
                            vdh.clear()

                            vocs = self.logic.steadyState(
                                t_dwell=self.args.v_t,
                                NPLC=self.args.steadystate_nplc,
                                stepDelay=self.args.steadystate_step_delay,
                                sourceVoltage=False,
                                compliance=self.args.voltage_compliance_override,
                                senseRange="a",
                                setPoint=self.args.steadystate_i,
                                handler=vdh,
                            )
                            self.logic.registerMeasurements(vocs, "V_oc dwell")

                            self.logic.Voc = vocs[-1][
                                0
                            ]  # take the last measurement to be Voc
                            self.logic.mppt.Voc = self.logic.Voc
                            self.logic.f[
                                self.logic.position + "/" + self.logic.pixel
                            ].attrs["Voc"] = self.logic.Voc

                        if type(self.args.current_compliance_override) == float:
                            compliance = self.args.current_compliance_override
                        else:
                            compliance = (
                                self.logic.compliance_guess
                            )  # we have to just guess what the current complaince should be here
                            # TODO: probably need the user to tell us when it's a dark scan to get the sensativity we need in that case
                        self.logic.mppt.current_compliance = compliance

                        if self.args.sweep_1 is True:
                            # now sweep from Voc --> Isc
                            if type(self.args.scan_start_override_1) == float:
                                start = self.args.scan_start_override_1
                            else:
                                start = self.logic.Voc
                            if type(self.args.scan_end_override_1) == float:
                                end = self.args.scan_end_override_1
                            else:
                                end = 0

                            message = "Sweeping voltage from {:.0f} mV to {:.0f} mV".format(
                                start * 1000, end * 1000
                            )
                            # clear iv plot
                            ivdh.clear()
                            sv = self.logic.sweep(
                                sourceVoltage=True,
                                compliance=compliance,
                                senseRange="a",
                                nPoints=self.args.scan_points,
                                stepDelay=self.args.scan_step_delay,
                                start=start,
                                end=end,
                                NPLC=self.args.scan_nplc,
                                message=message,
                                handler=ivdh,
                            )
                            self.logic.registerMeasurements(sv, "Sweep")

                            (
                                Pmax_sweep,
                                Vmpp,
                                Impp,
                                maxIndex,
                            ) = self.logic.mppt.which_max_power(sv)
                            self.logic.mppt.Vmpp = Vmpp

                            if type(self.args.current_compliance_override) == float:
                                compliance = self.args.current_compliance_override
                            else:
                                compliance = abs(
                                    sv[-1][1] * 2
                                )  # take the last measurement*2 to be our compliance limit
                            self.logic.mppt.current_compliance = compliance

                        if self.args.sweep_2:
                            if type(self.args.scan_start_override_2) == float:
                                start = self.args.scan_start_override_2
                            else:
                                start = 0
                            if type(self.args.scan_end_override_2) == float:
                                end = self.args.scan_end_override_2
                            else:
                                end = self.logic.Voc * (
                                    (100 + self.logic.percent_beyond_voc) / 100
                                )

                            message = "Snaithing voltage from {:.0f} mV to {:.0f} mV".format(
                                start * 1000, end * 1000
                            )

                            sv = self.logic.sweep(
                                sourceVoltage=True,
                                senseRange="f",
                                compliance=compliance,
                                nPoints=self.args.scan_points,
                                start=start,
                                end=end,
                                NPLC=self.args.scan_nplc,
                                message=message,
                                handler=ivdh,
                            )
                            self.logic.registerMeasurements(sv, "Snaith")
                            (
                                Pmax_snaith,
                                Vmpp,
                                Impp,
                                maxIndex,
                            ) = self.logic.mppt.which_max_power(sv)
                            if abs(Pmax_snaith) > abs(Pmax_sweep):
                                self.logic.mppt.Vmpp = Vmpp

                        if self.args.mppt_t > 0:
                            message = "Tracking maximum power point for {:} seconds".format(
                                self.args.mppt_t
                            )
                            # clear mppt plot
                            mdh.clear()
                            self.logic.track_max_power(
                                self.args.mppt_t,
                                message,
                                NPLC=self.args.steadystate_nplc,
                                stepDelay=self.args.steadystate_step_delay,
                                extra=self.args.mppt_params,
                                handler=mdh,
                            )

                        if self.args.i_t > 0:
                            # steady state I@constant V measured here - usually Isc
                            # clear I@constant V plot
                            cdh.clear()
                            iscs = self.logic.steadyState(
                                t_dwell=self.args.i_t,
                                NPLC=self.args.steadystate_nplc,
                                stepDelay=self.args.steadystate_step_delay,
                                sourceVoltage=True,
                                compliance=compliance,
                                senseRange="a",
                                setPoint=self.args.steadystate_v,
                                handler=cdh,
                            )
                            self.logic.registerMeasurements(iscs, "I_sc dwell")

                            self.logic.Isc = iscs[-1][
                                1
                            ]  # take the last measurement to be Isc
                            self.logic.f[
                                self.logic.position + "/" + self.logic.pixel
                            ].attrs["Isc"] = self.logic.Isc
                            self.logic.mppt.Isc = self.logic.Isc

                        if type(self.args.current_compliance_override) == float:
                            compliance = self.args.current_compliance_override
                        else:
                            # if the measured steady state Isc was below 5 microamps, set the compliance to 10uA (this is probaby a dark curve)
                            # we don't need the accuracy of the lowest current sense range (I think) and we'd rather have the compliance headroom
                            # otherwise, set it to be 2x of Isc
                            if abs(self.logic.Isc) < 0.000005:
                                compliance = 0.00001
                            else:
                                compliance = abs(self.logic.Isc * 2)
                        self.logic.mppt.current_compliance = compliance

                    self.logic.pixelComplete()

                for pixel in eqe_pixel_queue:
                    if self.args.calibrate_eqe is False:
                        substrate = pixel[0][0].upper()
                        pix = pixel[0][1]
                        print(
                            "\nOperating on substrate {:s}, pixel {:s}...".format(
                                substrate, pix
                            )
                        )
                        # add id str to handlers to display on plots
                        edh.idn = f"substrate{substrate}_pixel{pix}"

                        if last_substrate != substrate:  # we have a new substrate
                            print('New substrate using "{:}" layout!'.format(pixel[3]))
                            last_substrate = substrate

                            substrate_ready = self.logic.substrateSetup(
                                position=substrate, layout_name=pixel[3],
                            )

                        pixel_ready = self.logic.pixelSetup(pixel)
                    else:
                        # move to eqe calibration photodiode
                        self.logic.me.goto(self.args.position_override)
                        pixel_ready = True
                        substrate_ready = True

                    if pixel_ready and substrate_ready:
                        message = f"Scanning EQE from {self.args.eqe_start_wl} nm to {self.args.eqe_end_wl} nm"
                        # clear eqe plot
                        edh.clear()
                        self.logic.eqe(
                            psu_ch1_voltage=self.args.psu_vs[0],
                            psu_ch1_current=self.args.psu_is[0],
                            psu_ch2_voltage=self.args.psu_vs[1],
                            psu_ch2_current=self.args.psu_is[1],
                            psu_ch3_voltage=self.args.psu_vs[2],
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

                    self.logic.pixelComplete()

                # clean up mqtt publishers
                if self.args.mqtt_host != "":
                    for dh in handlers:
                        dh.end_q()
                        dh.disconnect()

            self.logic.runDone()
        self.logic.sm.outOn(on=False)
        print("Program complete.")

    def buildQ(self, pixel_address_string, experiment):
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
        # get stage location for experiment centre
        experiment_centre = [int(x) for x in self.config["experiment_positions"][experiment].split(",")]

        # look up and calculate substrate centre info relative to experiment centre
        substrate_rows_cols = [int(x) for x in self.config[substrates]["number"].split(",")]
        substrate_rows = substrate_rows_cols[0]
        try:
            substrate_cols = substrate_rows_cols[1]
        except IndexError:
            # single column only
            substrate_cols = 1

        substrate_number = 1
        for x in substrate_rows_cols:
            substrate_number = substrate_number * x

        substrate_spacing = [int(x) for x in self.config[substrates]["spacing"].split(",")]

        substrate_centres = []
        for i in range(substrate_number):
            # TODO: finish getting substrate centres
            # TODO: add absolute calc

        # TODO: return support for inferring layout from pcb adapter resistors

        # make sure as many layouts as labels were given
        if (l1 := len(self.args.layouts)) != (l2 := len(self.args.labels)):
            raise ValueError(
                f"Lists of layouts and labels must have the same length. Layouts list has length {l1} and labels list has length {l2}."
            )

        # create a substrate queue where each element is a dictionary of info about the
        # layout from the config file
        substrate_q = []
        for layout, label in zip(self.args.layouts, self.args.labels):
            # get pcb adapter info from config file
            pcb_name = self.config[layout]["pcb_name"]

            # read in pixel positions from layout in config file
            config_pos = self.config[layout]["positions"].split(",")
            pixel_positions = []
            for i in range(0, len(config_pos), 2):
                pixel_positions.append(tuple(config_pos[i : i + 2]))

            substrate_dict = {
                "label": label,
                "layout": layout,
                "pcb_name": pcb_name,
                "pcb_contact_pads": self.config[pcb_name]["pcb_contact_pads"],
                "pcb_resistor": self.config[pcb_name]["pcb_resistor"],
                "pixels": self.config[layout]["pixels"].split(","),
                "pixel_positions": pixel_positions,
                "areas": self.config[layout]["areas"].split(","),
            }
            substrate_q.append(substrate_dict)

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
                    # TODO: get absolute pixel position
                    pixel_dict = {
                        "label": substrate["label"],
                        "pixel": pixel,
                        "position": substrate["pixel_positions"][pixel - 1],
                        "area": substrate["areas"][pixel - 1],
                    }
                    pixel_q.append(pixel_dict)

        # read pixel address string
        q = []
        if pixel_address_string[0:2] == "0x":
            bitmask = bytearray.fromhex(pixel_address_string[2:])
            for substrate_index, byte in enumerate(bitmask):
                substrate = chr(substrate_index + ord("A"))
                #  only put good pixels in the queue
                if substrate in self.logic.pcb.substratesConnected:
                    for i in range(8):
                        mask = 128 >> i
                        if byte & mask:
                            q.append(substrate + str(i + 1))
                else:
                    print("WARNING! Substrate {:} could not be found".format(substrate))
        else:
            pixels = [
                pixel_address_string[i : i + 2]
                for i in range(0, len(pixel_address_string), 2)
            ]
            for pixel in pixels:
                pixel_in_q = False
                if len(pixel) == 2:
                    pixel_int = int(pixel[1])
                    #  only put good pixels in the queue
                    if (pixel[0] in self.logic.pcb.substratesConnected) and (
                        pixel_int >= 1 and pixel_int <= 8
                    ):
                        q.append(pixel)
                        pixel_in_q = True
                if pixel_in_q is False:
                    print("WARNING! Discarded bad pixel address: {:}".format(pixel))

        # now we have a list of pixel addresses, q
        ret = []
        if len(q) > 0:
            using_layouts = {}

            # layout indicies given to us by the user
            user_layouts = deque(self.args.layout_index)

            substrates = [x[0] for x in q]
            substrates = sorted(set(substrates))

            for substrate in substrates:
                r_value = self.logic.pcb.resistors[substrate]
                valid_layouts = {}
                for key, value in self.layouts.items():
                    targets = value["adapterboardresistor"]
                    for target in targets:
                        if (
                            fabric.isWithinPercent(target, r_value)
                            or self.args.ignore_adapter_resistors
                            or target == 0
                        ):
                            valid_layouts[key] = value
                            break

                # here's the layout the user selected for this substrate
                user_layout = user_layouts[0]

                # rotate the deque
                user_layouts.rotate(-1)

                if user_layout in valid_layouts:
                    using_layouts[substrate] = valid_layouts[user_layout]
                elif len(valid_layouts) == 1:
                    using_layouts[substrate] = valid_layouts.popitem()[1]
                else:
                    raise ValueError(
                        "Could not determine the layout for substrate {:}. Use the -i argument with one of the following values {:}".format(
                            substrate, list(valid_layouts.keys())
                        )
                    )

            # device areas given to us by the user
            user_areas = deque(self.args.area)
            for pxad in q:
                this_substrate = pxad[0]
                this_pixel = int(pxad[1])
                area = using_layouts[this_substrate]["pixelareas"][this_pixel - 1]

                # absolute position for this pixel
                position = (
                    self.logic.me.substrate_centers[ord(this_substrate) - ord("A")]
                    + using_layouts[this_substrate]["pixelpositions"][this_pixel - 1]
                )
                if len(user_areas) > 0:
                    print(
                        "WARNING: Overriding pixel {:}'s area value with {:} cm^2".format(
                            pxad, user_areas[0]
                        )
                    )
                    area = user_areas[
                        0
                    ]  # here's the area the user selected for this pixel
                    user_areas.rotate(-1)  # rotate the deque
                if area == 0:
                    print("INFO: Skipping zero area pixel: {:}".format(pxad))
                else:
                    final_element = (
                        pxad,
                        area,
                        position,
                        using_layouts[this_substrate]["name"],
                    )
                    ret.append(final_element)

        return deque(ret)

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

    def get_args(self):
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
            help="*List of substrate layout names to use for finding pixel informatio from the configuration file",
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
            "--light-address",
            type=str,
            action=self.RecordPref,
            default="wavelabs-relay://localhost:3335",
            help="*protocol://hostname:port for communication with the solar simulator, 'none' for no light, 'wavelabs://0.0.0.0:3334' for starting a wavelabs server on port 3334, 'wavelabs-relay://127.0.0.1:3335' for connecting to a wavelabs-relay server",
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
            "--motion-address",
            type=str,
            action=self.RecordPref,
            default="none",
            help="*protocol://hostname:port for communication with the motion controller, 'none' for no motion, 'afms:///dev/ttyAMC0' for an Adafruit Arduino motor shield on /dev/ttyAMC0, 'env://FTDI_DEVICE' to read the address from an environment variable named FTDI_DEVICE",
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
            "--sm-address",
            default="GPIB0::24::INSTR",
            type=str,
            action=self.RecordPref,
            help="*VISA resource name for sourcemeter",
        )
        setup.add_argument(
            "--pcb-address",
            type=str,
            default="10.42.0.54:23",
            action=self.RecordPref,
            help="*host:port for PCB comms",
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
            "--visa-lib",
            type=str,
            action=self.RecordPref,
            default="@py",
            help="*Path to visa library in case pyvisa can't find it, try C:\\Windows\\system32\\visa64.dll",
        )
        setup.add_argument(
            "--gui-address",
            type=str,
            default="http://127.0.0.1:51246",
            action=self.RecordPref,
            help="*protocol://host:port for the gui server",
        )
        setup.add_argument(
            "--lia-address",
            default="TCPIP::10.0.0.1:INSTR",
            type=str,
            action=self.RecordPref,
            help="*VISA resource name for lock-in amplifier",
        )
        setup.add_argument(
            "--lia-output-interface",
            default=0,
            type=int,
            action=self.RecordPref,
            help="Lock-in amplifier output inface: 0 = RS232 (default), 1 = GPIB",
        )
        setup.add_argument(
            "--mono-address",
            default="TCPIP::10.0.0.2:INSTR",
            type=str,
            action=self.RecordPref,
            help="*VISA resource name for monochromator",
        )
        setup.add_argument(
            "--psu-address",
            default="TCPIP::10.0.0.3:INSTR",
            type=str,
            action=self.RecordPref,
            help="*VISA resource name for bias LED PSU",
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
            help="Override position given by pixel selection and use these coordinates instead",
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
    with cli() as cli:
        cli.run()

