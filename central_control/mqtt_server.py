"""MQTT Server for interacting with the system."""

import argparse
import collections
import itertools
import multiprocessing
import os
import pickle
import sys
import time
import types
import uuid
import warnings

import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
import numpy as np
import yaml

from mqtt_tools.queue_publisher import MQTTQueuePublisher
from central_control.fabric import fabric


def get_args():
    """Get arguments parsed from the command line."""
    parser = argparse.ArgumentParser()
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

    # inject dummy key if necessary
    request = args[0]
    if "dummy" not in request["args"].keys():
        request["args"]["dummy"] = False
        args = (
            request,
            args[1],
        )

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
        payload = {"level": "warning", "msg": "Measurement server busy!"}
        publish.single("log", pickle.dumps(payload), qos=2, hostname=cli_args.mqtthost)


def stop_process():
    """Stop a running process."""
    global process

    if process.is_alive() is True:
        process.terminate()
    else:
        payload = {
            "level": "warning",
            "msg": "Nothing to stop. Measurement server is idle.",
        }
        publish.single("log", pickle.dumps(payload), qos=2, hostname=cli_args.mqtthost)


def _calibrate_eqe(request, mqtthost):
    """Measure the EQE reference photodiode.

    Parameters
    ----------
    request : dict
        Request dictionary sent to the server.
    mqtthost : str
        MQTT broker IP address or hostname.
    """
    print("calibrating eqe...")
    with fabric() as measurement, MQTTQueuePublisher() as mqttc:
        # create temporary mqtt client
        mqttc.will_set("measurement/status", pickle.dumps("Ready"), 2, True)
        mqttc.run(mqtthost)

        _log("Calibrating EQE...", "info", **{"mqttc": mqttc})

        args = request["args"]

        # get pixel queue
        if int(args["eqe_devs"], 16) > 0:
            # if the bitmask isn't empty
            try:
                pixel_queue = _build_q(request, experiment="eqe")
            except ValueError as e:
                # there was a problem with the labels and/or layouts list
                _log("CALIBRATION ABORTED! " + str(e), "error", **{"mqttc": mqttc})
                return
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

        _eqe(pixel_queue, request, measurement, mqttc, calibration=True)

        _log("EQE calibration complete!", "info", **{"mqttc": mqttc})

    print("EQE calibration finished.")


def _calibrate_psu(request, mqtthost):
    """Measure the reference photodiode as a funtcion of LED current.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    request : dict
        Request dictionary sent to the server.
    """
    print("Calibrating psu...")

    with fabric() as measurement, MQTTQueuePublisher() as mqttc:
        mqttc.will_set("measurement/status", pickle.dumps("Ready"), 2, True)
        mqttc.run(mqtthost)

        _log("Calibration LED PSU...", "info", **{"mqttc": mqttc})

        config = request["config"]
        args = request["args"]

        # get pixel queue
        if int(args["eqe_devs"], 16) > 0:
            # if the bitmask isn't empty
            try:
                pixel_queue = _build_q(request, experiment="eqe")
            except ValueError as e:
                # there was a problem with the labels and/or layouts list
                _log("CALIBRATION ABORTED! " + str(e), "error", **{"mqttc": mqttc})
                return
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
            dummy=args["dummy"],
            visa_lib=config["visa"]["visa_lib"],
            smu_address=config["smu"]["address"],
            smu_terminator=config["smu"]["terminator"],
            smu_baud=config["smu"]["baud"],
            smu_front_terminals=config["smu"]["front_terminals"],
            smu_two_wire=config["smu"]["two_wire"],
            pcb_address=config["controller"]["uri"],
            motion_address=config["stage"]["uri"],
            psu_address=config["psu"]["address"],
            psu_terminator=config["psu"]["terminator"],
            psu_baud=config["psu"]["baud"],
        )

        # using smu to measure the current from the photodiode
        measurement.set_experiment_relay("iv")

        last_label = None
        while len(pixel_queue) > 0:
            pixel = pixel_queue.popleft()
            label = pixel["label"]
            pix = pixel["pixel"]
            _log(
                f"\nOperating on substrate {label}, pixel {pix}...",
                "info",
                **{"mqttc": mqttc},
            )

            # add id str to handlers to display on plots
            idn = f"{label}_pixel{pix}"

            print(pixel)

            # we have a new substrate
            if last_label != label:
                _log(
                    f"New substrate using '{pixel['layout']}' layout!",
                    "info",
                    **{"mqttc": mqttc},
                )
                last_label = label

            # move to pixel
            resp = measurement.pixel_setup(
                pixel, handler=_handle_stage_data, handler_kwargs={"mqttc": mqttc}
            )

            if resp != 0:
                _log(
                    f"Stage/mux error: {resp}! Aborting calibration!",
                    "error",
                    **{"mqttc": mqttc},
                )
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
                mqttc.append_payload(
                    f"calibration/psu/ch{channel}", pickle.dumps(diode_dict)
                )

        _log("LED PSU calibration complete!", "info", **{"mqttc": mqttc})

    print("Finished calibrating PSU.")


def _calibrate_spectrum(request, mqtthost):
    """Measure the solar simulator spectrum using it's internal spectrometer.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    request : dict
        Request dictionary sent to the server.
    """
    print("Calibrating spectrum...")

    with fabric() as measurement, MQTTQueuePublisher() as mqttc:
        mqttc.will_set("measurement/status", pickle.dumps("Ready"), 2, True)
        mqttc.run(mqtthost)

        _log("Calibrating solar simulator spectrum...", "info", **{"mqttc": mqttc})

        config = request["config"]
        args = request["args"]

        measurement.connect_instruments(
            dummy=args["dummy"],
            visa_lib=config["visa"]["visa_lib"],
            light_address=config["solarsim"]["uri"],
        )

        timestamp = time.time()

        spectrum = measurement.measure_spectrum()

        # update spectrum  calibration data in atomic thread-safe way
        spectrum_dict = {"data": spectrum, "timestamp": timestamp}

        # publish calibration
        mqttc.append_payload("calibration/spectrum", pickle.dumps(spectrum_dict))

        _log(
            "Finished calibrating solar simulator spectrum!", "info", **{"mqttc": mqttc}
        )

    print("Spectrum calibration complete.")


def _calibrate_solarsim_diodes(request, mqtthost):
    """Calibrate the solar simulator using photodiodes.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    request : dict
        Request dictionary sent to the server.
    """
    print("calibrating solar sim diodes")

    with fabric() as measurement, MQTTQueuePublisher() as mqttc:
        mqttc.will_set("measurement/status", pickle.dumps("Ready"), 2, True)
        mqttc.run(mqtthost)

        _log("Calibrating solar simulator diodes...", "info", **{"mqttc": mqttc})

        args = request["args"]

        # get pixel queue
        if int(args["iv_devs"], 16) > 0:
            # if the bitmask isn't empty
            try:
                pixel_queue = _build_q(request, experiment="eqe")
            except ValueError as e:
                # there was a problem with the labels and/or layouts list
                _log("CALIBRATION ABORTED! " + str(e), "error", **{"mqttc": mqttc})
                return
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

        try:
            _ivt(pixel_queue, request, measurement, mqttc, calibration=True)
        except ValueError as e:
            _log("CALIBRATION ABORTED! " + str(e), "error", **{"mqttc": mqttc})
            return

        _log("Solar simulator diode calibration complete!", "info", **{"mqttc": mqttc})

    print("Solar sim diode calibration complete.")


def _calibrate_rtd(request, mqtthost):
    """Calibrate RTD's for temperature measurement.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    request : dict
        Request dictionary sent to the server.
    """
    print("Calibrating rtds...")

    with fabric() as measurement, MQTTQueuePublisher() as mqttc:
        mqttc.will_set("measurement/status", pickle.dumps("Ready"), 2, True)
        mqttc.run(mqtthost)

        _log("Calibrating RTDs...", "info", **{"mqttc": mqttc})

        request["args"]["i_dwell"] = 0
        request["args"]["v_dwell"] = 0
        request["args"]["mppt_dwell"] = 0

        args = request["args"]

        # get pixel queue
        if int(args["iv_devs"], 16) > 0:
            # if the bitmask isn't empty
            try:
                pixel_queue = _build_q(request, experiment="eqe")
            except ValueError as e:
                # there was a problem with the labels and/or layouts list
                _log("CALIBRATION ABORTED! " + str(e), "error", **{"mqttc": mqttc})
                return
        else:
            # if it's emptpy, report error
            _log(
                "CALIBRATION ABORTED! No devices selected.", "error", **{"mqttc": mqttc}
            )

        try:
            _ivt(pixel_queue, request, measurement, mqttc, calibration=True, rtd=True)
        except ValueError as e:
            _log("CALIBRATION ABORTED! " + str(e), "error", **{"mqttc": mqttc})
            return

        _log("RTD calibration complete!", "info", **{"mqttc": mqttc})

    print("RTD calibration complete.")


def _home(request, mqtthost):
    """Home the stage.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    request : dict
        Request dictionary sent to the server.
    """
    print("Homing...")

    with fabric() as measurement, MQTTQueuePublisher() as mqttc:
        mqttc.will_set("measurement/status", pickle.dumps("Ready"), 2, True)
        mqttc.run(mqtthost)

        _log("Homing stage...", "info", **{"mqttc": mqttc})

        config = request["config"]
        args = request["args"]

        measurement.connect_instruments(
            dummy=args["dummy"],
            pcb_address=config["controller"]["uri"],
            motion_address=config["stage"]["uri"],
        )

        homed = measurement.home_stage()

        if isinstance(homed, list):
            _log(f"Stage lengths: {homed}", "info", **{"mqttc": mqttc})
        else:
            _log(f"Home failed with result: {homed}", "error", **{"mqttc": mqttc})

        _log("Homing complete!", "info", **{"mqttc": mqttc})

    print("Homing complete.")


def _goto(request, mqtthost):
    """Go to a stage position.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    request : dict
        Request dictionary sent to the server.
    """
    print("Goto...")

    with fabric() as measurement, MQTTQueuePublisher() as mqttc:
        mqttc.will_set("measurement/status", pickle.dumps("Ready"), 2, True)
        mqttc.run(mqtthost)

        _log(f"Moving to stage position...", "info", **{"mqttc": mqttc})

        args = request["args"]
        position = [args["goto_x"], args["goto_y"]]

        config = request["config"]
        args = request["args"]

        measurement.connect_instruments(
            dummy=args["dummy"],
            pcb_address=config["controller"]["uri"],
            motion_address=config["stage"]["uri"],
        )

        goto = measurement.goto_stage_position(position)

        if goto < 0:
            _log(f"Goto failed with result: {goto}", "error", **{"mqttc": mqttc})

        _log("Goto complete!", "info", **{"mqttc": mqttc})

    print("Goto complete.")


def _read_stage(request, mqtthost):
    """Read the stage position.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    request : dict
        Request dictionary sent to the server.
    """
    print("Reading stage...")

    with fabric() as measurement, MQTTQueuePublisher() as mqttc:
        mqttc.will_set("measurement/status", pickle.dumps("Ready"), 2, True)
        mqttc.run(mqtthost)

        _log(f"Reading stage position...", "info", **{"mqttc": mqttc})

        config = request["config"]
        args = request["args"]

        measurement.connect_instruments(
            dummy=args["dummy"],
            pcb_address=config["controller"]["uri"],
            motion_address=config["stage"]["uri"],
        )

        stage_pos = measurement.read_stage_position()

        if isinstance(stage_pos, list):
            _log(f"Stage lengths: {stage_pos}", "info", **{"mqttc": mqttc})
        else:
            _log(
                f"Read position failed with result: {stage_pos}",
                "error",
                **{"mqttc": mqttc},
            )

        _log("Read complete!", "info", **{"mqttc": mqttc})

    print("Read stage complete.")


def _contact_check(request, mqtthost):
    """Perform contact check.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    request : dict
        Request dictionary sent to the server.
    """
    print("Performing contact check...")

    with fabric() as measurement, MQTTQueuePublisher() as mqttc:
        mqttc.will_set("measurement/status", pickle.dumps("Ready"), 2, True)
        mqttc.run(mqtthost)

        _log("Performing contact check...", "info", **{"mqttc": mqttc})

        args = request["args"]
        config = request["config"]

        measurement.connect_instruments(
            dummy=args["dummy"],
            visa_lib=config["visa"]["visa_lib"],
            smu_address=config["smu"]["address"],
            smu_terminator=config["smu"]["terminator"],
            smu_baud=config["smu"]["baud"],
            smu_front_terminals=config["smu"]["front_terminals"],
            smu_two_wire=config["smu"]["two_wire"],
            pcb_address=config["controller"]["uri"],
            motion_address=config["stage"]["uri"],
        )

        # make a pixel queue for the contact check
        try:
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
        except ValueError as e:
            # there was a problem with the labels and/or layouts list
            _log("CONTACT CHECK ABORTED! " + str(e), "error", **{"mqttc": mqttc})
            return

        response = measurement.contact_check(
            iv_pixel_queue, _handle_contact_check, {"mqttc": mqttc}
        )
        _log(response, "info", **{"mqttc": mqttc})

        _log("Contact check complete!", "info", **{"mqttc": mqttc})

    print("Contact check complete.")


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

    # read in substrate spacing in mm along each axis into a list
    substrate_spacing = config["substrates"]["spacing"]

    # get absolute substrate centres along each axis
    axis_pos = []
    for offset, spacing, number, centre in zip(
        substrate_offsets, substrate_spacing, substrate_number, experiment_centre,
    ):
        abs_offset = offset * spacing
        axis_pos.append(np.linspace(-abs_offset + centre, abs_offset + centre, number))

    # create array of positions
    substrate_centres = list(itertools.product(*axis_pos))

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
            abs_pixel_position = [int(x + y) for x, y in zip(pos, centre)]
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


def _handle_measurement_data(data, **kwargs):
    """Publish measurement data.

    Parameters
    ----------
    data : list
        List of data to publish.
    **kwargs : dict
        Dictionary of additional keyword arguments required by handler.
    """
    kind = kwargs["kind"]
    idn = kwargs["idn"]
    pixel = kwargs["pixel"]
    try:
        sweep = kwargs["sweep"]
    except KeyError:
        sweep = ""
    mqttc = kwargs["mqttc"]

    payload = {
        "data": data,
        "idn": idn,
        "pixel": pixel,
        "clear": False,
        "end": False,
        "sweep": sweep,
    }
    mqttc.append_payload(f"data/raw/{kind}", pickle.dumps(payload))


def _handle_stage_data(data, **kwargs):
    """Publish stage position data.

    Parameters
    ----------
    data : list
        List of data to publish.
    **kwargs : dict
        Dictionary of additional keyword arguments required by handler.
    """
    mqttc = kwargs["mqttc"]

    mqttc.append_payload("stage_position", pickle.dumps(data))


def _handle_contact_check(pixel_msg, **kwargs):
    """Publish stage position data.

    Parameters
    ----------
    settings : dict
        Dictionary of save settings.
    **kwargs : dict
        Dictionary of additional keyword arguments required by handler.
    """
    mqttc = kwargs["mqttc"]

    mqttc.append_payload("contact_check", pickle.dumps(pixel_msg))


def _log(msg, level, **kwargs):
    """Publish info for logging.

    Parameters
    ----------
    msg : str
        Log message.
    level : str
        Log level.
    **kwargs : dict
        Dictionary of additional keyword arguments required by handler.
    """
    mqttc = kwargs["mqttc"]

    payload = {"level": level, "msg": msg}
    mqttc.append_payload("log", pickle.dumps(payload))


def _ivt(pixel_queue, request, measurement, mqttc, calibration=False, rtd=False):
    """Run through pixel queue of i-v-t measurements.

    Paramters
    ---------
    pixel_queue : deque of dict
        Queue of dictionaries of pixels to measure.
    request : dict
        Experiment arguments.
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    measurement : measurement logic object
        Object controlling instruments and measurements.
    calibration : bool
        Calibration flag.
    rtd : bool
        RTD flag for type of calibration. Used for reporting.
    """
    config = request["config"]
    args = request["args"]

    # connect instruments
    measurement.connect_instruments(
        dummy=args["dummy"],
        visa_lib=config["visa"]["visa_lib"],
        smu_address=config["smu"]["address"],
        smu_terminator=config["smu"]["terminator"],
        smu_baud=config["smu"]["baud"],
        smu_front_terminals=config["smu"]["front_terminals"],
        smu_two_wire=config["smu"]["two_wire"],
        pcb_address=config["controller"]["uri"],
        motion_address=config["stage"]["uri"],
        light_address=config["solarsim"]["uri"],
    )

    # set the master experiment relay
    resp = measurement.set_experiment_relay("iv")

    if resp != 0:
        _log(f"Stage/mux error: {resp}! Aborting run", "error", **{"mqttc": mqttc})
        return

    if args["ad_switch"] is True:
        source_delay = -1
    else:
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
        _log(
            f"\nOperating on substrate {label}, pixel {pix}...",
            "info",
            **{"mqttc": mqttc},
        )

        print(f"{pixel}")

        # add id str to handlers to display on plots
        idn = f"{label}_pixel{pix}"

        # check if there is have a new substrate
        if last_label != label:
            _log(
                f"New substrate using '{pixel['layout']}' layout!",
                "info",
                **{"mqttc": mqttc},
            )
            last_label = label

        # move to pixel
        resp = measurement.pixel_setup(
            pixel, handler=_handle_stage_data, handler_kwargs={"mqttc": mqttc}
        )

        if resp != 0:
            _log(f"Stage/mux error: {resp}! Aborting run", "error", **{"mqttc": mqttc})
            break

        # init parameters derived from steadystate measurements
        ssvoc = None

        # get or estimate compliance current
        compliance_i = measurement.compliance_current_guess(pixel["area"])

        # choose data handler
        if calibration is False:
            handler = _handle_measurement_data
            handler_kwargs = {"idn": idn, "pixel": pixel, "mqttc": mqttc}
        else:
            handler = None
            handler_kwargs = {}

        timestamp = time.time()

        # turn on light
        measurement.le.on()

        # steady state v@constant I measured here - usually Voc
        if args["i_dwell"] > 0:
            # clear v@constant I plot
            mqttc.append_payload("plot/vt/clear", pickle.dumps(""))
            print("i_dwell")
            if calibration is False:
                handler_kwargs["kind"] = "vt_measurement"

            vt = measurement.steady_state(
                t_dwell=args["i_dwell"],
                NPLC=args["nplc"],
                stepDelay=source_delay,
                sourceVoltage=False,
                compliance=3,
                senseRange="a",
                setPoint=args["i_dwell_value"],
                handler=handler,
                handler_kwargs=handler_kwargs,
            )

            data += vt

            # if this was at Voc, use the last measurement as estimate of Voc
            if args["i_dwell_value"] == 0:
                ssvoc = vt[-1]
                measurement.mppt.Voc = ssvoc

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
            # clear iv plot
            mqttc.append_payload("plot/iv/clear", pickle.dumps(""))

            if sweep == "dark":
                measurement.le.off()
                sense_range = "a"
            else:
                sense_range = "f"

            if args["sweep_check"] is True:
                print("sweep 1")
                start = args["sweep_start"]
                end = args["sweep_end"]

                _log(
                    f"Sweeping voltage from {start} V to {end} V",
                    "info",
                    **{"mqttc": mqttc},
                )

                if calibration is False:
                    handler_kwargs["kind"] = "iv_measurement"
                    handler_kwargs["sweep"] = sweep

                iv1 = measurement.sweep(
                    sourceVoltage=True,
                    compliance=compliance_i,
                    senseRange=sense_range,
                    nPoints=args["iv_steps"],
                    stepDelay=source_delay,
                    start=start,
                    end=end,
                    NPLC=args["nplc"],
                    handler=handler,
                    handler_kwargs=handler_kwargs,
                )

                data += iv1

                Pmax_sweep1, Vmpp1, Impp1, maxIx1 = measurement.mppt.which_max_power(
                    iv1
                )

            if args["return_switch"] is True:
                print("sweep 2")
                # sweep the opposite way to sweep 1
                start = end
                end = start

                _log(
                    f"Sweeping voltage from {start} V to {end} V",
                    "info",
                    **{"mqttc": mqttc},
                )

                if calibration is False:
                    handler_kwargs["kind"] = "iv_measurement"
                    handler_kwargs["sweep"] = sweep

                iv2 = measurement.sweep(
                    sourceVoltage=True,
                    senseRange=sense_range,
                    compliance=compliance_i,
                    nPoints=args["iv_steps"],
                    stepDelay=source_delay,
                    start=start,
                    end=end,
                    NPLC=args["nplc"],
                    handler=handler,
                    handler_kwargs=handler_kwargs,
                )

                data += iv2

                Pmax_sweep2, Vmpp2, Impp2, maxIx2 = measurement.mppt.which_max_power(
                    iv2
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
        measurement.mppt.current_compliance = compliance_i

        if args["mppt_dwell"] > 0:
            _log(
                f"Tracking maximum power point for {args['mppt_dwell']} seconds.",
                "info",
                **{"mqttc": mqttc},
            )
            print("mppt dwell")
            # clear mppt plot
            mqttc.append_payload("plot/mppt/clear", pickle.dumps(""))

            if calibration is False:
                handler_kwargs["kind"] = "mppt_measurement"

            # measure voc for 1s to initialise mppt
            vt = measurement.steady_state(
                t_dwell=1,
                NPLC=args["nplc"],
                stepDelay=args["source_delay"],
                sourceVoltage=False,
                compliance=3,
                senseRange="a",
                setPoint=0,
                handler=handler,
                handler_kwargs=handler_kwargs,
            )
            measurement.mppt.Voc = vt[-1][0]

            mt = measurement.track_max_power(
                args["mppt_dwell"],
                NPLC=args["nplc"],
                step_delay=args["source_delay"],
                extra=args["mppt_params"],
                handler=handler,
                handler_kwargs=handler_kwargs,
            )

            data += vt
            data += mt

        if args["v_dwell"] > 0:
            # steady state I@constant V measured here - usually Isc
            # clear I@constant V plot
            mqttc.append_payload("plot/it/clear", pickle.dumps(""))

            print("v_dwell")

            if calibration is False:
                handler_kwargs["kind"] = "it_measurement"

            it = measurement.steady_state(
                t_dwell=args["v_dwell"],
                NPLC=args["nplc"],
                stepDelay=source_delay,
                sourceVoltage=True,
                compliance=compliance_i,
                senseRange="a",
                setPoint=args["v_dwell_value"],
                handler=handler,
                handler_kwargs=handler_kwargs,
            )

            data += it

        measurement.sm.outOn(False)

        if calibration is True:
            diode_dict = {"data": data, "timestamp": timestamp, "diode": idn}
            if rtd is True:
                mqttc.append_payload("calibration/rtd", pickle.dumps(diode_dict))
            else:
                mqttc.append_payload(
                    "calibration/solarsim_diode", pickle.dumps(diode_dict)
                )


def _eqe(pixel_queue, request, measurement, mqttc, calibration=False):
    """Run through pixel queue of EQE measurements.

    Paramters
    ---------
    pixel_queue : deque of dict
        Queue of dictionaries of pixels to measure.
    request : dict
        Experiment arguments.
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    measurement : measurement logic object
        Object controlling instruments and measurements.
    calibration : bool
        Calibration flag.
    """
    config = request["config"]
    args = request["args"]

    # connect instruments
    measurement.connect_instruments(
        dummy=args["dummy"],
        visa_lib=config["visa"]["visa_lib"],
        smu_address=config["smu"]["address"],
        smu_terminator=config["smu"]["terminator"],
        smu_baud=config["smu"]["baud"],
        smu_front_terminals=config["smu"]["front_terminals"],
        smu_two_wire=config["smu"]["two_wire"],
        pcb_address=config["controller"]["uri"],
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

    measurement.set_experiment_relay("eqe")

    resp = measurement.goto_stage_position(
        config["stage"]["experiment_positions"]["eqe"]
    )

    if resp != 0:
        _log(f"Stage/mux error: {resp}! Aborting run!", "error", **{"mqttc": mqttc})
        return

    print(pixel_queue)
    time.sleep(10)

    last_label = None
    while len(pixel_queue) > 0:
        pixel = pixel_queue.popleft()
        label = pixel["label"]
        pix = pixel["pixel"]
        _log(
            f"Operating on substrate {label}, pixel {pix}...",
            "info",
            **{"mqttc": mqttc},
        )

        print(pixel)

        # add id str to handlers to display on plots
        idn = f"{label}_pixel{pix}"

        # we have a new substrate
        if last_label != label:
            _log(
                f"New substrate using '{pixel['layout']}' layout!",
                "info",
                **{"mqttc": mqttc},
            )
            last_label = label

        # move to pixel
        resp = measurement.pixel_setup(
            pixel, handler=_handle_stage_data, handler_kwargs={"mqttc": mqttc}
        )

        if resp != 0:
            _log(f"Stage/mux error: {resp}! Aborting run!", "error", **{"mqttc": mqttc})
            break

        _log(
            f"Scanning EQE from {args['eqe_start']} nm to {args['eqe_end']} nm",
            "info",
            **{"mqttc": mqttc},
        )

        # determine how live measurement data will be handled
        if calibration is True:
            handler = None
            handler_kwargs = {}
        else:
            handler = _handle_measurement_data
            handler_kwargs = {
                "kind": "eqe_measurement",
                "idn": idn,
                "pixel": pixel,
                "mqttc": mqttc,
            }

        # clear eqe plot
        mqttc.append_payload("plot/eqe/clear", pickle.dumps(""))

        # get human-readable timestamp
        timestamp = time.time()

        num_points = 1 + int(
            np.absolute(args["eqe_end"] - args["eqe_start"]) / args["eqe_step"]
        )

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
            num_points=num_points,
            grating_change_wls=config["monochromator"]["grating_change_wls"],
            filter_change_wls=config["monochromator"]["filter_change_wls"],
            integration_time=args["eqe_int"],
            handler=handler,
            handler_kwargs=handler_kwargs,
        )

        # update eqe diode calibration data in
        if calibration is True:
            diode_dict = {"data": eqe, "timestamp": timestamp, "diode": idn}
            mqttc.append_payload(
                "calibration/eqe", pickle.dumps(diode_dict), retain=True,
            )


def _run(request, mqtthost):
    """Act on command line instructions.

    Parameters
    ----------
    request : dict
        Dictionary of configuration settings and measurement arguments.
    mqtthost : str
        MQTT broker IP address or host name.
    """
    print("Running measurement...")

    args = request["args"]

    # calibrate spectrum if required
    if args["iv_devs"] is not None:
        _calibrate_spectrum(request, mqtthost)

    with fabric() as measurement, MQTTQueuePublisher() as mqttc:
        mqttc.will_set("measurement/status", pickle.dumps("Ready"), 2, True)
        mqttc.run(mqtthost)

        _log("Starting run...", "info", **{"mqttc": mqttc})

        if args["iv_devs"] is not None:
            try:
                iv_pixel_queue = _build_q(request, experiment="solarsim")
            except ValueError as e:
                # there was a problem with the labels and/or layouts list
                _log("RUN ABORTED! " + str(e), "error", **{"mqttc": mqttc})
                return
        else:
            iv_pixel_queue = []

        if args["eqe_devs"] is not None:
            try:
                eqe_pixel_queue = _build_q(request, experiment="eqe")
            except ValueError as e:
                _log("RUN ABORTED! " + str(e), "error", **{"mqttc": mqttc})
                return
        else:
            eqe_pixel_queue = []

        print(iv_pixel_queue)
        print(eqe_pixel_queue)

        # measure i-v-t
        if len(iv_pixel_queue) > 0:
            try:
                _ivt(iv_pixel_queue, request, measurement, mqttc)
            except ValueError as e:
                _log("RUN ABORTED! " + str(e), "error", **{"mqttc": mqttc})
                return

        # measure eqe
        if len(eqe_pixel_queue) > 0:
            _eqe(eqe_pixel_queue, request, measurement, mqttc)

        # report complete
        _log("Run complete!", "info", **{"mqttc": mqttc})

        # close mqtt client cleanly

    print("Measurement complete.")


def on_message(mqttc, obj, msg):
    """Act on an MQTT message.

    Actions that require instrument I/O run in a worker process. Only one action
    process can run at a time. If an action process is running the server will
    report that it's busy.
    """
    request = pickle.loads(msg.payload)

    print(request)

    # perform a requested action
    if (action := msg.topic.split("/")[-1]) == "run":
        start_process(_run, (request, cli_args.mqtthost,))
    elif action == "stop":
        stop_process()
    elif action == "calibrate_eqe":
        start_process(_calibrate_eqe, (request, cli_args.mqtthost,))
    elif action == "calibrate_psu":
        start_process(_calibrate_psu, (request, cli_args.mqtthost,))
    elif action == "calibrate_solarsim_diodes":
        start_process(_calibrate_solarsim_diodes, (request, cli_args.mqtthost,))
    elif action == "calibrate_spectrum":
        start_process(_calibrate_spectrum, (request, cli_args.mqtthost,))
    elif action == "calibrate_rtd":
        start_process(_calibrate_rtd, (request, cli_args.mqtthost,))
    elif action == "contact_check":
        start_process(_contact_check, (request, cli_args.mqtthost,))
    elif action == "home":
        start_process(_home, (request, cli_args.mqtthost,))
    elif action == "goto":
        start_process(_goto, (request, cli_args.mqtthost,))
    elif action == "read_stage":
        start_process(_read_stage, (request, cli_args.mqtthost,))


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
    publish.single(
        "measurement/status", pickle.dumps("Ready"), qos=2, hostname=cli_args.mqtthost,
    )
    print(f"{client_id} connected!")
    mqttc.loop_forever()
