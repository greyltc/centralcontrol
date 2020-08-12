"""MQTT Server for interacting with the system."""

import argparse
import collections
import itertools
import multiprocessing
import os
import pickle
import queue
import signal
import sys
import threading
import time
import traceback
import types
import uuid
import warnings

import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
import numpy as np
import yaml

from mqtt_tools.queue_publisher import MQTTQueuePublisher
from central_control_dev.fabric import fabric


def get_args():
    """Get arguments parsed from the command line."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-d",
        "--dummy",
        default=False,
        action="store_true",
        help="Run the server in dummy mode using virtual instruments.",
    )
    parser.add_argument(
        "--mqtthost",
        default="127.0.0.1",
        help="IP address or hostname of MQTT broker.",
    )
    return parser.parse_args()


def start_process(target, args):
    """Start a new process to perform an action if no process is running.

    Parameters
    ----------
    target : function handle
        Function to run in child process.
    args : tuple
        Arguments required by the function.
    """
    global process

    if process.is_alive() is False:
        process = multiprocessing.Process(target=target, args=args)
        process.start()
        publish.single(
            "measurement/status",
            pickle.dumps("Busy"),
            qos=2,
            retain=True,
            hostname=cli_args.mqtthost,
        )
    else:
        payload = {"level": 30, "msg": "Measurement server busy!"}
        publish.single(
            "measurement/log", pickle.dumps(payload), qos=2, hostname=cli_args.mqtthost
        )


def stop_process():
    """Stop a running process."""
    global process

    if process.is_alive() is True:
        os.kill(process.pid, signal.SIGINT)
        process.terminate()
        payload = {
            "level": 20,
            "msg": "Request to stop completed!",
        }
        publish.single(
            "measurement/log", pickle.dumps(payload), qos=2, hostname=cli_args.mqtthost
        )
        publish.single(
            "measurement/status",
            pickle.dumps("Ready"),
            qos=2,
            retain=True,
            hostname=cli_args.mqtthost,
        )
    else:
        payload = {
            "level": 30,
            "msg": "Nothing to stop. Measurement server is idle.",
        }
        publish.single(
            "measurement/log", pickle.dumps(payload), qos=2, hostname=cli_args.mqtthost
        )


def _calibrate_eqe(request, mqtthost, dummy):
    """Measure the EQE reference photodiode.

    Parameters
    ----------
    request : dict
        Request dictionary sent to the server.
    mqtthost : str
        MQTT broker IP address or hostname.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    """
    print("calibrating eqe...")

    # catch all errors and report back to log
    try:
        with fabric() as measurement, MQTTQueuePublisher() as mqttc:
            # create temporary mqtt client
            mqttc.connect(mqtthost)
            mqttc.loop_start()

            _log("Calibrating EQE...", 20, mqttc)

            args = request["args"]

            # get pixel queue
            if int(args["eqe_devs"], 16) > 0:
                pixel_queue = _build_q(request, experiment="eqe")

                if len(pixel_queue) > 1:
                    _log(
                        "Only one diode can be calibrated at a time but "
                        + f"{len(pixel_queue)} were given. Only the first diode will be "
                        + "measured.",
                        30,
                        mqttc,
                    )

                    # only take first pixel for a calibration
                    pixel_dict = pixel_queue[0]
                    pixel_queue = collections.deque(maxlen=1)
                    pixel_queue.append(pixel_dict)
            else:
                # if it's emptpy, assume cal diode is connected externally
                pixel_dict = {
                    "label": args["label_tree"][0],
                    "layout": None,
                    "sub_name": None,
                    "pixel": 0,
                    "position": None,
                    "area": None,
                }
                pixel_queue = collections.deque(pixel_dict)

            _eqe(pixel_queue, request, measurement, mqttc, dummy, calibration=True)

            _log("EQE calibration complete!", 20, mqttc)

        print("EQE calibration finished.")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        publish.single(
            "measurement/log",
            {"msg": f"EQE CALIBRATION ABORTED! {type(e)} " + str(e), "level": 40},
            qos=2,
            hostname=mqtthost,
        )

    publish.single(
        "measurement/status",
        pickle.dumps("Ready"),
        qos=2,
        retain=True,
        hostname=mqtthost,
    )


def _calibrate_psu(request, mqtthost, dummy):
    """Measure the reference photodiode as a funtcion of LED current.

    Parameters
    ----------
    request : dict
        Request dictionary sent to the server.
    mqtthost : str
        MQTT broker IP address or hostname.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    """
    print("Calibrating psu...")

    try:
        with fabric() as measurement, MQTTQueuePublisher() as mqttc:
            mqttc.connect(mqtthost)
            mqttc.loop_start()

            _log("Calibration LED PSU...", 20, mqttc)

            config = request["config"]
            args = request["args"]

            # get pixel queue
            if int(args["eqe_devs"], 16) > 0:
                pixel_queue = _build_q(request, experiment="eqe")
            else:
                # if it's emptpy, assume cal diode is connected externally
                pixel_dict = {
                    "label": args["label_tree"][0],
                    "layout": None,
                    "sub_name": None,
                    "pixel": 0,
                    "position": None,
                    "area": None,
                }
                pixel_queue = collections.deque(pixel_dict)

            # connect instruments
            measurement.connect_instruments(
                dummy=dummy,
                visa_lib=config["visa"]["visa_lib"],
                smu_address=config["smu"]["address"],
                smu_terminator=config["smu"]["terminator"],
                smu_baud=config["smu"]["baud"],
                smu_front_terminals=config["smu"]["front_terminals"],
                smu_two_wire=config["smu"]["two_wire"],
                pcb_address=config["controller"]["address"],
                motion_address=config["stage"]["uri"],
                psu_address=config["psu"]["address"],
                psu_terminator=config["psu"]["terminator"],
                psu_baud=config["psu"]["baud"],
            )

            # using smu to measure the current from the photodiode
            resp = measurement.set_experiment_relay("iv")

            if resp != "":
                _log(
                    f"Experiment relay error: {resp}! Aborting run", 40, mqttc,
                )
                return

            last_label = None
            while len(pixel_queue) > 0:
                pixel = pixel_queue.popleft()
                label = pixel["label"]
                pix = pixel["pixel"]
                _log(
                    f"Operating on substrate {label}, pixel {pix}...", 20, mqttc,
                )

                # add id str to handlers to display on plots
                idn = f"{label}_pixel{pix}"

                print(pixel)

                # we have a new substrate
                if last_label != label:
                    _log(
                        f"New substrate using '{pixel['layout']}' layout!", 20, mqttc,
                    )
                    last_label = label

                # move to pixel
                resp = measurement.goto_pixel(pixel)
                if resp != 0:
                    _log(f"Stage error: {resp}! Aborting run!", 40, mqttc)
                    break

                resp = measurement.select_pixel(pixel)
                if resp != 0:
                    _log(f"Mux error: {resp}! Aborting run!", 40, mqttc)
                    break

                timestamp = time.time()

                # perform measurement
                for channel in [1, 2, 3]:
                    psu_calibration = measurement.calibrate_psu(
                        channel,
                        config["psu"]["calibration"]["max_current"],
                        config["psu"]["calibration"]["current_step"],
                    )

                    # update eqe diode calibration data in atomic thread-safe way
                    diode_dict = {
                        "data": psu_calibration,
                        "timestamp": timestamp,
                        "diode": idn,
                    }
                    if channel == 3:
                        retain = True
                    else:
                        retain = False
                    mqttc.append_payload(
                        f"calibration/psu/ch{channel}", pickle.dumps(diode_dict), retain
                    )

            _log("LED PSU calibration complete!", 20, mqttc)
        print("Finished calibrating PSU.")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        publish.single(
            "measurement/log",
            {"msg": f"PSU CALIBRATION ABORTED! {type(e)} " + str(e), "level": 40},
            qos=2,
            hostname=mqtthost,
        )

    publish.single(
        "measurement/status",
        pickle.dumps("Ready"),
        qos=2,
        retain=True,
        hostname=mqtthost,
    )


def _calibrate_spectrum(request, mqtthost, dummy):
    """Measure the solar simulator spectrum using it's internal spectrometer.

    Parameters
    ----------
    request : dict
        Request dictionary sent to the server.
    mqtthost : str
        MQTT broker IP address or hostname.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    """
    print("Calibrating spectrum...")

    try:
        with fabric() as measurement, MQTTQueuePublisher() as mqttc:
            mqttc.connect(mqtthost)
            mqttc.loop_start()

            _log("Calibrating solar simulator spectrum...", 20, mqttc)

            config = request["config"]
            args = request["args"]

            measurement.connect_instruments(
                dummy=dummy,
                visa_lib=config["visa"]["visa_lib"],
                light_address=config["solarsim"]["uri"],
                light_recipe=args["light_recipe"],
            )

            timestamp = time.time()

            spectrum = measurement.measure_spectrum()

            # update spectrum  calibration data in atomic thread-safe way
            spectrum_dict = {"data": spectrum, "timestamp": timestamp}

            # publish calibration
            mqttc.append_payload(
                "calibration/spectrum", pickle.dumps(spectrum_dict), retain=True
            )

            _log("Finished calibrating solar simulator spectrum!", 20, mqttc)

        print("Spectrum calibration complete.")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        publish.single(
            "measurement/log",
            {"msg": f"SPECTRUM CALIBRATION ABORTED! {type(e)} " + str(e), "level": 40},
            qos=2,
            hostname=mqtthost,
        )

    publish.single(
        "measurement/status",
        pickle.dumps("Ready"),
        qos=2,
        retain=True,
        hostname=mqtthost,
    )


def _calibrate_solarsim_diodes(request, mqtthost, dummy):
    """Calibrate the solar simulator using photodiodes.

    Parameters
    ----------
    request : dict
        Request dictionary sent to the server.
    mqtthost : str
        MQTT broker IP address or hostname.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    """
    print("calibrating solar sim diodes")

    try:
        with fabric() as measurement, MQTTQueuePublisher() as mqttc:
            mqttc.connect(mqtthost)
            mqttc.loop_start()

            _log("Calibrating solar simulator diodes...", 20, mqttc)

            args = request["args"]

            # get pixel queue
            if int(args["iv_devs"], 16) > 0:
                # if the bitmask isn't empty
                pixel_queue = _build_q(request, experiment="eqe")
            else:
                # if it's emptpy, assume cal diode is connected externally
                pixel_dict = {
                    "label": args["label_tree"][0],
                    "layout": None,
                    "sub_name": None,
                    "pixel": 0,
                    "position": None,
                    "area": None,
                }
                pixel_queue = collections.deque(pixel_dict)

            _ivt(pixel_queue, request, measurement, mqttc, dummy, calibration=True)

            _log("Solar simulator diode calibration complete!", 20, mqttc)

        print("Solar sim diode calibration complete.")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        publish.single(
            "measurement/log",
            {
                "msg": f"SOLARSIM DIODE CALIBRATION ABORTED! {type(e)} " + str(e),
                "level": 40,
            },
            qos=2,
            hostname=mqtthost,
        )

    publish.single(
        "measurement/status",
        pickle.dumps("Ready"),
        qos=2,
        retain=True,
        hostname=mqtthost,
    )


def _calibrate_rtd(request, mqtthost, dummy):
    """Calibrate RTD's for temperature measurement.

    Parameters
    ----------
    request : dict
        Request dictionary sent to the server.
    mqtthost : str
        MQTT broker IP address or hostname.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    """
    print("Calibrating rtds...")

    try:
        with fabric() as measurement, MQTTQueuePublisher() as mqttc:
            mqttc.connect(mqtthost)
            mqttc.loop_start()

            _log("Calibrating RTDs...", 20, mqttc)

            request["args"]["i_dwell"] = 0
            request["args"]["v_dwell"] = 0
            request["args"]["mppt_dwell"] = 0

            args = request["args"]

            # get pixel queue
            if int(args["iv_devs"], 16) > 0:
                # if the bitmask isn't empty
                pixel_queue = _build_q(request, experiment="eqe")
            else:
                # if it's emptpy, report error
                _log("CALIBRATION ABORTED! No devices selected.", 40, mqttc)

            _ivt(
                pixel_queue,
                request,
                measurement,
                mqttc,
                dummy,
                calibration=True,
                rtd=True,
            )

            _log("RTD calibration complete!", 20, mqttc)

        print("RTD calibration complete.")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        publish.single(
            "measurement/log",
            {"msg": f"RTD CALIBRATION ABORTED! {type(e)} " + str(e), "level": 40},
            qos=2,
            hostname=mqtthost,
        )

    publish.single(
        "measurement/status",
        pickle.dumps("Ready"),
        qos=2,
        retain=True,
        hostname=mqtthost,
    )


def _home(request, mqtthost, dummy):
    """Home the stage.

    Parameters
    ----------
    request : dict
        Request dictionary sent to the server.
    mqtthost : str
        MQTT broker IP address or hostname.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    """
    print("Homing...")

    try:
        with fabric() as measurement, MQTTQueuePublisher() as mqttc:
            mqttc.connect(mqtthost)
            mqttc.loop_start()

            _log("Homing stage...", 20, mqttc)

            config = request["config"]

            measurement.connect_instruments(
                dummy=dummy,
                pcb_address=config["controller"]["address"],
                motion_address=config["stage"]["uri"],
            )

            homed = measurement.home_stage()

            if isinstance(homed, list):
                _log(f"Stage lengths: {homed}", 20, mqttc)
            else:
                _log(f"Home failed with result: {homed}", 40, mqttc)

            _log("Homing complete!", 20, mqttc)

        print("Homing complete.")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        publish.single(
            "measurement/log",
            {"msg": f"HOMING ABORTED! {type(e)} " + str(e), "level": 40},
            qos=2,
            hostname=mqtthost,
        )

    publish.single(
        "measurement/status",
        pickle.dumps("Ready"),
        qos=2,
        retain=True,
        hostname=mqtthost,
    )


def _goto(request, mqtthost, dummy):
    """Go to a stage position.

    Parameters
    ----------
    request : dict
        Request dictionary sent to the server.
    mqtthost : str
        MQTT broker IP address or hostname.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    """
    print("Goto...")

    try:
        with fabric() as measurement, MQTTQueuePublisher() as mqttc:
            mqttc.connect(mqtthost)
            mqttc.loop_start()

            _log(f"Moving to stage position...", 20, mqttc)

            args = request["args"]
            position = [args["goto_x"], args["goto_y"], args["goto_z"]]

            config = request["config"]
            args = request["args"]

            measurement.connect_instruments(
                dummy=dummy,
                pcb_address=config["controller"]["address"],
                motion_address=config["stage"]["uri"],
            )

            goto = measurement.goto_stage_position(position)

            if goto < 0:
                _log(f"Goto failed with result: {goto}", 40, mqttc)

            _log("Goto complete!", 20, mqttc)

        print("Goto complete.")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        publish.single(
            "measurement/log",
            {"msg": f"GOTO ABORTED! {type(e)} " + str(e), "level": 40},
            qos=2,
            hostname=mqtthost,
        )

    publish.single(
        "measurement/status",
        pickle.dumps("Ready"),
        qos=2,
        retain=True,
        hostname=mqtthost,
    )


def _read_stage(request, mqtthost, dummy):
    """Read the stage position.

    Parameters
    ----------
    request : dict
        Request dictionary sent to the server.
    mqtthost : str
        MQTT broker IP address or hostname.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    """
    print("Reading stage...")

    try:
        with fabric() as measurement, MQTTQueuePublisher() as mqttc:
            mqttc.connect(mqtthost)
            mqttc.loop_start()

            _log(f"Reading stage position...", 20, mqttc)

            config = request["config"]

            measurement.connect_instruments(
                dummy=dummy,
                pcb_address=config["controller"]["address"],
                motion_address=config["stage"]["uri"],
            )

            stage_pos = measurement.read_stage_position()

            if isinstance(stage_pos, list):
                _log(f"Stage lengths: {stage_pos}", 20, mqttc)
            else:
                _log(
                    f"Read position failed with result: {stage_pos}", 40, mqttc,
                )

            _log("Read complete!", 20, mqttc)

        print("Read stage complete.")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        publish.single(
            "measurement/log",
            {"msg": f"READ STAGE ABORTED! {type(e)} " + str(e), "level": 40},
            qos=2,
            hostname=mqtthost,
        )

    publish.single(
        "measurement/status",
        pickle.dumps("Ready"),
        qos=2,
        retain=True,
        hostname=mqtthost,
    )


def _contact_check(request, mqtthost, dummy):
    """Perform contact check.

    Parameters
    ----------
    request : dict
        Request dictionary sent to the server.
    mqtthost : str
        MQTT broker IP address or hostname.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    """
    print("Performing contact check...")

    try:
        with fabric() as measurement, MQTTQueuePublisher() as mqttc:
            mqttc.connect(mqtthost)
            mqttc.loop_start()

            _log("Performing contact check...", 20, mqttc)

            args = request["args"]
            config = request["config"]

            measurement.connect_instruments(
                dummy=dummy,
                visa_lib=config["visa"]["visa_lib"],
                smu_address=config["smu"]["address"],
                smu_terminator=config["smu"]["terminator"],
                smu_baud=config["smu"]["baud"],
                smu_front_terminals=config["smu"]["front_terminals"],
                smu_two_wire=config["smu"]["two_wire"],
                pcb_address=config["controller"]["address"],
                motion_address=config["stage"]["uri"],
            )

            # make a pixel queue for the contact check
            # get length of bitmask string
            b_len = len(args["iv_devs"])

            # convert it to a string formatter for later
            # hash (#) appends 0x for hex
            # leading zero adds zero padding to resulting string
            # x formats as hexadecimal
            b_len_str = f"#0{b_len}x"

            # convert iv and eqe bitmasks to ints and perform bitwise or. This gets
            # pixels selected in either bitmask.
            iv_int = int(args["iv_devs"], 16)
            eqe_int = int(args["eqe_devs"], 16)
            # bitwise or
            merge_int = iv_int | eqe_int

            # convert int back to bitmask, overriding iv_pixel_address for build_q
            args["iv_devs"] = format(merge_int, b_len_str)

            iv_pixel_queue = _build_q(request, experiment="solarsim")

            response = measurement.contact_check(
                iv_pixel_queue, _handle_contact_check, {"mqttc": mqttc}
            )

            print(response)

            _log(response, 20, mqttc)

            _log("Contact check complete!", 20, mqttc)

        print("Contact check complete.")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        publish.single(
            "measurement/log",
            {"msg": f"CONTACT CHECK ABORTED! {type(e)} " + str(e), "level": 40},
            qos=2,
            hostname=mqtthost,
        )

    publish.single(
        "measurement/status",
        pickle.dumps("Ready"),
        qos=2,
        retain=True,
        hostname=mqtthost,
    )


def _get_substrate_positions(config, experiment):
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
    experiment_centre = config["stage"]["experiment_positions"][experiment]
    print(f"{experiment} center: {experiment_centre}")

    # read in number substrates in the array along each axis
    substrate_number = config["substrates"]["number"]

    # get number of substrate centres between the centre and the edge of the
    # substrate array along each axis, e.g. if there are 4 rows, there are 1.5
    # substrate centres to the outermost substrate
    substrate_offsets = []
    substrate_total = 1
    for number in substrate_number:
        if number % 2 == 0:
            offset = number / 2 - 0.5
        else:
            offset = np.floor(number / 2)
        substrate_offsets.append(offset)
        substrate_total = substrate_total * number

    print(f"Substrate offsets: {substrate_offsets}")

    # read in substrate spacing in mm along each axis into a list
    substrate_spacing = config["substrates"]["spacing"]

    # get absolute substrate centres along each axis
    axis_pos = []
    for offset, spacing, number, centre in zip(
        substrate_offsets, substrate_spacing, substrate_number, experiment_centre,
    ):
        abs_offset = offset * spacing
        print(f"Offset from experiment centre: {abs_offset}")
        axis_pos.append(np.linspace(-abs_offset + centre, abs_offset + centre, number))

    print(f"Positions along each axis: {axis_pos}")

    # create array of positions
    substrate_centres = []
    n_axes = len(axis_pos)
    if n_axes == 2:
        for y in axis_pos[1]:
            substrate_centres += [[x, y] for x in axis_pos[0]]
    elif n_axes == 1:
        substrate_centres = [[x] for x in axis_pos[0]]

    print(f"Substrate centres (absolute): {substrate_centres}")

    return substrate_centres


def _build_q(request, experiment):
    """Generate a queue of pixels to run through.

    Parameters
    ----------
    args : types.SimpleNamespace
        Experiment arguments.
    experiment : str
        Name used to look up the experiment centre stage position from the config
        file.

    Returns
    -------
    pixel_q : deque
        Queue of pixels to measure.
    """
    # TODO: return support for inferring layout from pcb adapter resistors

    config = request["config"]
    args = request["args"]

    # get substrate centres
    substrate_centres = _get_substrate_positions(config, experiment)
    substrate_total = len(substrate_centres)

    # number of substrates along each available axis
    substrate_number = config["substrates"]["number"]

    # make sure as many layouts as labels were given
    if (l := len(args["label_tree"])) != substrate_total:
        raise ValueError(
            "Lists of layouts and labels must match number of substrates in the "
            + f"array: {substrate_total}. Layouts list has length {l}."
        )

    layout = config["substrates"]["active_layout"]

    if experiment == "solarsim":
        pixel_address_string = args["iv_devs"]
    elif experiment == "eqe":
        pixel_address_string = args["eqe_devs"]

    # create a substrate queue where each element is a dictionary of info about the
    # layout from the config file
    substrate_q = []
    i = 0
    for label, centre, sub_name in zip(
        args["label_tree"], substrate_centres, args["subs_names"]
    ):
        # get pcb adapter info from config file
        pcb_name = config["substrates"]["layouts"][layout]["pcb_name"]

        # read in pixel positions from layout in config file
        config_pos = config["substrates"]["layouts"][layout]["positions"]
        pixel_positions = []
        for pos in config_pos:
            abs_pixel_position = [x + y for x, y in zip(pos, centre)]
            pixel_positions.append(abs_pixel_position)

        substrate_dict = {
            "label": label,
            "sub_name": sub_name,
            "layout": layout,
            "pcb_name": pcb_name,
            "pcb_contact_pads": config["substrates"]["adapters"][pcb_name][
                "pcb_contact_pads"
            ],
            "pcb_resistor": config["substrates"]["adapters"][pcb_name]["pcb_resistor"],
            "pixels": config["substrates"]["layouts"][layout]["pixels"],
            "pixel_positions": pixel_positions,
            "areas": config["substrates"]["layouts"][layout]["areas"],
        }
        substrate_q.append(substrate_dict)

        i += 1

    # TODO: return support for pixel strings that aren't hex bitmasks
    # convert hex bitmask string into bit list where 1's and 0's represent whether
    # a pixel should be measured or not, respectively
    b_len = len(bin(16 ** (len(pixel_address_string) - 2) - 1))
    fmt = f"#0{b_len}b"
    bitmask = format(int(pixel_address_string, 16), fmt)
    bitmask = [int(x) for x in bitmask[2:]]
    bitmask.reverse()

    # build pixel queue
    pixel_q = collections.deque()
    for substrate in substrate_q:
        # git bitmask for the substrate pcb
        sub_bitmask = [bitmask.pop(0) for i in range(substrate["pcb_contact_pads"])]
        # select pixels to measure from layout
        for pixel in substrate["pixels"]:
            if sub_bitmask[pixel - 1] == 1:
                pixel_dict = {
                    "label": substrate["label"],
                    "layout": substrate["layout"],
                    "sub_name": substrate["sub_name"],
                    "pixel": pixel,
                    "position": substrate["pixel_positions"][pixel - 1],
                    "area": substrate["areas"][pixel - 1],
                }
                pixel_q.append(pixel_dict)

    return pixel_q


class DataHandler:
    """Handler for measurement data."""

    def __init__(self, kind="", pixel={}, sweep="", mqttqp=None, no_plot=False):
        """Construct data handler object.

        Parameters
        ----------
        kind : str
            Kind of measurement data. This is used as a sub-channel name.
        pixel : dict
            Pixel information.
        sweep : {"", "dark", "light"}
            If the handler is for sweep data, specify whether the sweep is under
            "light" or "dark" conditions.
        mqttqp : MQTTQueuePublisher
            MQTT queue publisher object that publishes measurement data.
        no_plot : bool
            Flag whether or not handled data should be plotted by plotter client.
        """
        self.kind = kind
        self.pixel = pixel
        self.sweep = sweep
        self.mqttqp = mqttqp
        self.no_plot = no_plot

    def handle_data(self, data):
        """Handle measurement data.

        Parameters
        ----------
        data : array-like
            Measurement data.
        """
        payload = {
            "data": data,
            "pixel": self.pixel,
            "sweep": self.sweep,
            "no_plot": self.no_plot,
        }
        self.mqttqp.append_payload(f"data/raw/{self.kind}", pickle.dumps(payload))


class ContactCheckHandler:
    """Handler for contact check msgs."""

    def __init__(self, mqttqp=None):
        """Construct data handler object.

        Parameters
        ----------
        mqttqp : MQTTQueuePublisher
            MQTT queue publisher object that publishes measurement data.
        """
        self.mqttqp = mqttqp

    def handle_contact_check(self, msg):
        """Handle contact check message.

        Parameters
        ----------
        msg : str
            Failure message.
        """
        self.mqttqp.append_payload("contact_check", pickle.dumps(msg))


def _clear_plot(kind, mqttqp):
    """Publish measurement data.

    Parameters
    ----------
    kind : str
        Kind of measurement data. This is used as a sub-channel name.
    mqttqp : MQTTQueuePublisher
        MQTT queue publisher object that publishes measurement data.
    """
    payload = ""
    mqttqp.append_payload(f"plotter/{kind}/clear", pickle.dumps(payload))


def _log(msg, level, mqttqp):
    """Publish info for logging.

    Parameters
    ----------
    msg : str
        Log message.
    level : int
        Log level used by logging module:

            * 50 : CRITICAL
            * 40 : ERROR
            * 30 : WARNING
            * 20 : INFO
            * 10 : DEBUG
            * 0 : NOTSET

    mqttqp : MQTTQueuePublisher
        MQTT queue publisher object that publishes measurement data.
    """
    payload = {"level": level, "msg": msg}
    mqttqp.append_payload("measurement/log", pickle.dumps(payload))


def _ivt(
    pixel_queue, request, measurement, mqttc, dummy=False, calibration=False, rtd=False
):
    """Run through pixel queue of i-v-t measurements.

    Paramters
    ---------
    pixel_queue : deque of dict
        Queue of dictionaries of pixels to measure.
    request : dict
        Experiment arguments.
    measurement : measurement logic object
        Object controlling instruments and measurements.
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    calibration : bool
        Calibration flag.
    rtd : bool
        RTD flag for type of calibration. Used for reporting.
    """
    config = request["config"]
    args = request["args"]

    # connect instruments
    measurement.connect_instruments(
        dummy=dummy,
        visa_lib=config["visa"]["visa_lib"],
        smu_address=config["smu"]["address"],
        smu_terminator=config["smu"]["terminator"],
        smu_baud=config["smu"]["baud"],
        smu_front_terminals=config["smu"]["front_terminals"],
        smu_two_wire=config["smu"]["two_wire"],
        pcb_address=config["controller"]["address"],
        motion_address=config["stage"]["uri"],
        light_address=config["solarsim"]["uri"],
        light_recipe=args["light_recipe"],
    )

    # set the master experiment relay
    resp = measurement.set_experiment_relay("iv")

    if resp != "":
        _log(f"Experiment relay error: {resp}! Aborting run", 40, mqttc)
        return

    source_delay = args["source_delay"]

    last_label = None
    # scan through the pixels and do the requested measurements
    while len(pixel_queue) > 0:
        # instantiate container for all measurement data on pixel
        data = []

        # get pixel info
        pixel = pixel_queue.popleft()
        label = pixel["label"]
        pix = pixel["pixel"]
        print(f"Operating on substrate {label}, pixel {pix}...")

        print(f"{pixel}")

        # add id str to handlers to display on plots
        idn = f"{label}_pixel{pix}"

        # check if there is have a new substrate
        if last_label != label:
            print(f"New substrate using '{pixel['layout']}' layout!")
            last_label = label

        # move to pixel
        resp = measurement.goto_pixel(pixel)
        if resp != 0:
            _log(f"Stage error: {resp}! Aborting run", 40, mqttc)
            break

        # select pixel
        resp = measurement.select_pixel(pixel)
        if resp != 0:
            _log(f"Mux error: {resp}! Aborting run", 40, mqttc)
            break

        # init parameters derived from steadystate measurements
        ssvoc = None

        # get or estimate compliance current
        compliance_i = measurement.compliance_current_guess(pixel["area"])
        measurement.mppt.current_compliance = compliance_i

        # choose data handler
        if calibration is False:
            dh = DataHandler(pixel=pixel, mqttqp=mqttc)
            handler = dh.handle_data
        else:
            handler = lambda x: None

        timestamp = time.time()

        # turn on light
        measurement.le.on()

        # steady state v@constant I measured here - usually Voc
        if args["i_dwell"] > 0:
            _log(
                f"Measuring voltage output at constant current on {idn}.", 20, mqttc,
            )

            if calibration is False:
                kind = "vt_measurement"
                dh.kind = kind
                _clear_plot(kind, mqttc)

            vt = measurement.steady_state(
                t_dwell=args["i_dwell"],
                NPLC=args["nplc"],
                sourceVoltage=False,
                compliance=3,
                senseRange="a",
                setPoint=args["i_dwell_value"],
                handler=handler,
            )

            data += vt

            # if this was at Voc, use the last measurement as estimate of Voc
            if args["i_dwell_value"] == 0:
                ssvoc = vt[-1][0]
            else:
                ssvoc = None

        # if performing sweeps
        if args["sweep_check"] is True:
            # detmine type of sweeps to perform
            if (s := args["lit_sweep"]) == 0:
                sweeps = ["dark", "light"]
            elif s == 1:
                sweeps = ["light", "dark"]
            elif s == 2:
                sweeps = ["dark"]
            elif s == 3:
                sweeps = ["light"]
        else:
            sweeps = []

        # perform sweeps
        for sweep in sweeps:
            if sweep == "dark":
                measurement.le.off()
                sense_range = "a"
            else:
                sense_range = "f"

            if calibration is False:
                kind = "iv_measurement"
                dh.kind = kind
                dh.sweep = sweep
                _clear_plot(kind, mqttc)

            if args["sweep_check"] is True:
                _log(
                    f"Performing {sweep} sweep 1 on {idn}.", 20, mqttc,
                )
                start = args["sweep_start"]
                end = args["sweep_end"]

                print(f"Sweeping voltage from {start} V to {end} V")

                iv1 = measurement.sweep(
                    sourceVoltage=True,
                    compliance=compliance_i,
                    senseRange=sense_range,
                    nPoints=int(args["iv_steps"]),
                    stepDelay=source_delay,
                    start=start,
                    end=end,
                    NPLC=args["nplc"],
                    handler=handler,
                )

                data += iv1

                Pmax_sweep1, Vmpp1, Impp1, maxIx1 = measurement.mppt.register_curve(
                    iv1, light=(sweep == "light")
                )

            if args["return_switch"] is True:
                _log(
                    f"Performing {sweep} sweep 2 on {idn}.", 20, mqttc,
                )
                # sweep the opposite way to sweep 1
                start = args["sweep_end"]
                end = args["sweep_start"]

                print(f"Sweeping voltage from {start} V to {end} V")

                iv2 = measurement.sweep(
                    sourceVoltage=True,
                    senseRange=sense_range,
                    compliance=compliance_i,
                    nPoints=int(args["iv_steps"]),
                    stepDelay=source_delay,
                    start=start,
                    end=end,
                    NPLC=args["nplc"],
                    handler=handler,
                )

                data += iv2

                Pmax_sweep2, Vmpp2, Impp2, maxIx2 = measurement.mppt.register_curve(
                    iv2, light=(sweep == "light")
                )

            if sweep == "dark":
                measurement.le.on()

        # TODO: read and interpret parameters for smart mode
        # # determine Vmpp and current compliance for mppt
        # if (self.args["sweep_check"] is True) & (self.args["return_switch"] is True):
        #     if abs(Pmax_sweep1) > abs(Pmax_sweep2):
        #         Vmpp = Vmpp1
        #         compliance_i = Impp1 * 5
        #     else:
        #         Vmpp = Vmpp2
        #         compliance_i = Impp2 * 5
        # elif self.args["sweep_check"] is True:
        #     Vmpp = Vmpp1
        #     compliance_i = Impp1 * 5
        # else:
        #     # no sweeps have been measured so max power tracker will estimate Vmpp
        #     # based on Voc (or measure it if also no Voc) and will use initial
        #     # compliance set before any measurements were taken.
        #     Vmpp = None
        # self.logic.mppt.Vmpp = Vmpp

        if args["mppt_dwell"] > 0:
            _log(
                f"Performing max. power tracking on {idn}.", 20, mqttc,
            )

            print(f"Tracking maximum power point for {args['mppt_dwell']} seconds.")

            if calibration is False:
                kind = "mppt_measurement"
                dh.kind = kind
                _clear_plot(kind, mqttc)

            if ssvoc is not None:
                # tell the mppt what our measured steady state Voc was
                measurement.mppt.Voc = ssvoc

            (mt, vt) = measurement.track_max_power(
                args["mppt_dwell"],
                NPLC=args["nplc"],
                extra=args["mppt_params"],
                handler=handler,
            )

            if calibration is False and len(vt) > 0:
                dh.kind = "vt_measurement"
                # don't plot the voc at the beginning of mppt
                dh.no_plot = True
                for d in vt:
                    handler(d)
                # reset the flag
                dh.no_plot = False

            data += vt
            data += mt

        if args["v_dwell"] > 0:
            _log(
                f"Measuring output current and constant voltage on {idn}.", 20, mqttc,
            )

            if calibration is False:
                kind = "it_measurement"
                dh.kind = kind
                _clear_plot(kind, mqttc)

            it = measurement.steady_state(
                t_dwell=args["v_dwell"],
                NPLC=args["nplc"],
                sourceVoltage=True,
                compliance=compliance_i,
                senseRange="a",
                setPoint=args["v_dwell_value"],
                handler=handler,
            )

            data += it

        measurement.le.off()
        measurement.sm.outOn(False)

        if calibration is True:
            diode_dict = {"data": data, "timestamp": timestamp, "diode": idn}
            if rtd is True:
                print("RTD")
                mqttc.append_payload("calibration/rtd", pickle.dumps(diode_dict))
            else:
                mqttc.append_payload(
                    "calibration/solarsim_diode", pickle.dumps(diode_dict)
                )


def _eqe(pixel_queue, request, measurement, mqttc, dummy=False, calibration=False):
    """Run through pixel queue of EQE measurements.

    Paramters
    ---------
    pixel_queue : deque of dict
        Queue of dictionaries of pixels to measure.
    request : dict
        Experiment arguments.
    measurement : measurement logic object
        Object controlling instruments and measurements.
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    calibration : bool
        Calibration flag.
    """
    config = request["config"]
    args = request["args"]

    # connect instruments
    measurement.connect_instruments(
        dummy=dummy,
        visa_lib=config["visa"]["visa_lib"],
        smu_address=config["smu"]["address"],
        smu_terminator=config["smu"]["terminator"],
        smu_baud=config["smu"]["baud"],
        smu_front_terminals=config["smu"]["front_terminals"],
        smu_two_wire=config["smu"]["two_wire"],
        pcb_address=config["controller"]["address"],
        motion_address=config["stage"]["uri"],
        lia_address=config["lia"]["address"],
        lia_terminator=config["lia"]["terminator"],
        lia_baud=config["lia"]["baud"],
        lia_output_interface=config["lia"]["output_interface"],
        mono_address=config["monochromator"]["address"],
        mono_terminator=config["monochromator"]["terminator"],
        mono_baud=config["monochromator"]["baud"],
        psu_address=config["psu"]["address"],
    )

    resp = measurement.set_experiment_relay("eqe")

    if resp != "":
        _log(f"Experiment relay error: {resp}! Aborting run", 40, mqttc)
        return

    last_label = None
    while len(pixel_queue) > 0:
        pixel = pixel_queue.popleft()
        label = pixel["label"]
        pix = pixel["pixel"]

        # add id str to handlers to display on plots
        idn = f"{label}_pixel{pix}"

        _log(
            f"Measuring EQE on {idn}", 20, mqttc,
        )

        print(f"{pixel}")

        # we have a new substrate
        if last_label != label:
            print(f"New substrate using '{pixel['layout']}' layout!")
            last_label = label

        # move to pixel
        resp = measurement.goto_pixel(pixel)
        if resp != 0:
            _log(f"Stage error: {resp}! Aborting run!", 40, mqttc)
            break

        resp = measurement.select_pixel(pixel)
        if resp != 0:
            _log(f"Mux error: {resp}! Aborting run!", 40, mqttc)
            break

        _log(
            f"Scanning EQE from {args['eqe_start']} nm to {args['eqe_end']} nm",
            20,
            mqttc,
        )

        # determine how live measurement data will be handled
        if calibration is True:
            handler = lambda x: None
        else:
            kind = "eqe_measurement"
            dh = DataHandler(kind=kind, pixel=pixel, mqttqp=mqttc)
            handler = dh.handle_data
            _clear_plot(kind, mqttc)

        # get human-readable timestamp
        timestamp = time.time()

        # perform measurement
        eqe = measurement.eqe(
            psu_ch1_voltage=config["psu"]["ch1_voltage"],
            psu_ch1_current=args["chan1"],
            psu_ch2_voltage=config["psu"]["ch2_voltage"],
            psu_ch2_current=args["chan2"],
            psu_ch3_voltage=config["psu"]["ch3_voltage"],
            psu_ch3_current=args["chan3"],
            smu_voltage=args["eqe_bias"],
            start_wl=args["eqe_start"],
            end_wl=args["eqe_end"],
            num_points=int(args["eqe_step"]),
            grating_change_wls=config["monochromator"]["grating_change_wls"],
            filter_change_wls=config["monochromator"]["filter_change_wls"],
            integration_time=args["eqe_int"],
            handler=handler,
        )

        # update eqe diode calibration data in
        if calibration is True:
            diode_dict = {"data": eqe, "timestamp": timestamp, "diode": idn}
            mqttc.append_payload(
                "calibration/eqe", pickle.dumps(diode_dict), retain=True,
            )


def _run(request, mqtthost, dummy):
    """Act on command line instructions.

    Parameters
    ----------
    request : dict
        Request dictionary sent to the server.
    mqtthost : str
        MQTT broker IP address or hostname.
    dummy : bool
        Flag for dummy mode using virtual instruments.
    """
    print("Running measurement...")

    args = request["args"]

    # calibrate spectrum if required
    if args["iv_devs"] is not None:
        _calibrate_spectrum(request, mqtthost, dummy)

    publish.single(
        "measurement/status",
        pickle.dumps("Busy"),
        qos=2,
        retain=True,
        hostname=mqtthost,
    )

    try:
        with fabric() as measurement, MQTTQueuePublisher() as mqttc:
            mqttc.connect(mqtthost)
            mqttc.loop_start()

            _log("Starting run...", 20, mqttc)

            if args["iv_devs"] is not None:
                iv_pixel_queue = _build_q(request, experiment="solarsim")
            else:
                iv_pixel_queue = []

            if args["eqe_devs"] is not None:
                eqe_pixel_queue = _build_q(request, experiment="eqe")
            else:
                eqe_pixel_queue = []

            # measure i-v-t
            if len(iv_pixel_queue) > 0:
                _ivt(iv_pixel_queue, request, measurement, mqttc, dummy)
                measurement.disconnect_all_instruments()

            # measure eqe
            if len(eqe_pixel_queue) > 0:
                _eqe(eqe_pixel_queue, request, measurement, mqttc, dummy)

            # report complete
            _log("Run complete!", 20, mqttc)

        print("Measurement complete.")
    except KeyboardInterrupt:
        pass
    except Exception as e:
        traceback.print_exc()
        publish.single(
            "measurement/log",
            {"msg": f"RUN ABORTED! {type(e)} " + str(e), "level": 40},
            qos=2,
            hostname=mqtthost,
        )

    publish.single(
        "measurement/status",
        pickle.dumps("Ready"),
        qos=2,
        retain=True,
        hostname=mqtthost,
    )


# queue for storing incoming messages
msg_queue = queue.Queue()


def on_message(mqttc, obj, msg):
    """Add an MQTT message to the message queue."""
    msg_queue.put_nowait(msg)


def msg_handler():
    """Handle MQTT messages in the msg queue.

    This function should run in a separate thread, polling the queue for messages.

    Actions that require instrument I/O run in a worker process. Only one action
    process can run at a time. If an action process is running the server will
    report that it's busy.
    """
    while True:
        msg = msg_queue.get()

        request = pickle.loads(msg.payload)

        # perform a requested action
        if (action := msg.topic.split("/")[-1]) == "run":
            start_process(_run, (request, cli_args.mqtthost, cli_args.dummy,))
        elif action == "stop":
            stop_process()
        elif action == "calibrate_eqe":
            start_process(_calibrate_eqe, (request, cli_args.mqtthost, cli_args.dummy,))
        elif action == "calibrate_psu":
            start_process(_calibrate_psu, (request, cli_args.mqtthost, cli_args.dummy,))
        elif action == "calibrate_solarsim_diodes":
            start_process(
                _calibrate_solarsim_diodes,
                (request, cli_args.mqtthost, cli_args.dummy,),
            )
        elif action == "calibrate_spectrum":
            start_process(
                _calibrate_spectrum, (request, cli_args.mqtthost, cli_args.dummy,)
            )
        elif action == "calibrate_rtd":
            start_process(_calibrate_rtd, (request, cli_args.mqtthost, cli_args.dummy,))
        elif action == "contact_check":
            start_process(_contact_check, (request, cli_args.mqtthost, cli_args.dummy,))
        elif action == "home":
            start_process(_home, (request, cli_args.mqtthost, cli_args.dummy,))
        elif action == "goto":
            start_process(_goto, (request, cli_args.mqtthost, cli_args.dummy,))
        elif action == "read_stage":
            start_process(_read_stage, (request, cli_args.mqtthost, cli_args.dummy,))

        msg_queue.task_done()


# required when using multiprocessing in windows, advised on other platforms
if __name__ == "__main__":
    # get command line arguments
    cli_args = get_args()

    # create dummy process
    process = multiprocessing.Process()

    # create mqtt client id
    client_id = f"measure-{uuid.uuid4().hex}"

    # setup mqtt subscriber client
    mqttc = mqtt.Client(client_id=client_id)
    mqttc.will_set("measurement/status", pickle.dumps("Offline"), 2, retain=True)
    mqttc.on_message = on_message
    mqttc.connect(cli_args.mqtthost)
    mqttc.subscribe("measurement/#", qos=2)
    mqttc.loop_start()

    publish.single(
        "measurement/status",
        pickle.dumps("Ready"),
        qos=2,
        retain=True,
        hostname=cli_args.mqtthost,
    )

    print(f"{client_id} connected!")

    if cli_args.dummy is True:
        print("*** Running in dummy mode! ***")

    msg_handler()
