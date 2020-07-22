"""MQTT Server for interacting with the system."""

import argparse
import collections
import itertools
import json
import os
import sys
import time
import threading
import types
import warnings

from mqtt_tools.queue_publisher import MQTTQueuePublisher
import central_control.fabric

import numpy as np
import yaml


def get_args():
    """Get arguments parsed from the command line."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mqtthost",
        default="127.0.0.1",
        help="IP address or hostname of MQTT broker.",
    )
    return parser.parse_args()


def yaml_include(loader, node):
    """Load tagged yaml files into root file."""
    with open(node.value) as f:
        return yaml.load(f, Loader=yaml.FullLoader)


# bind include function to !include tags in yaml config file
yaml.add_constructor("!include", yaml_include)


def load_config_from_file(mqttc):
    """Load the configuration file into memory."""
    global config

    # try to load the configuration file from the current working directory
    try:
        with open("measurement_config.yaml", "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
        kind = "info"
        data = "Configuration file loaded successfully!"
    except FileNotFoundError:
        # maybe running a test from project directory
        try:
            with open("example_config.yaml", "r") as f:
                config = yaml.load(f, Loader=yaml.FullLoader)
            kind = "warning"
            data = (
                "'measurement_config.yaml' not found in server working directory: "
                + f"{os.getcwd()}. Falling back on 'example_config.yaml' project file."
            )
        except FileNotFoundError:
            kind = "error"
            data = (
                "No configuration file could be found in server working directory: "
                + f"{os.getcwd()}. Please run the server from a directory with a valid "
                + "'measurement_config.yaml' file."
            )

    mqttc.append_payload(json.dumps({"kind": kind, "data": data}))


def load_calibration_from_file(mqttc):
    """Load calibration data from file back into memory."""
    global calibration

    # try to load the configuration file from the current working directory
    try:
        with open("calibration.yaml", "r") as f:
            calibration = yaml.load(f, Loader=yaml.FullLoader)
        kind = "info"
        data = "Calibration data loaded successfully!"
    except FileNotFoundError:
        # no calibration available
        kind = "warning"
        data = "No calibration data available."

    mqttc.append_payload(json.dumps({"kind": kind, "data": data}))


def save_calibration_data():
    """Save calibration data to disk in case of crash."""
    with open("calibration.yaml", "w") as f:
        yaml.dump(calibration, f)


def start_thread(mqttc, target, args, name):
    """Start a new thread if no thread is running."""
    global thread

    if thread.is_alive() is False:
        thread = threading.Thread(target=target, args=args, name=name)
        thread.start()
        kind = "info"
        data = f"Started action: {thread.name}."
    else:
        kind = "warning"
        data = f"Server busy. Still running action: {thread.name}."

    # report back status to clients
    mqttc.append_payload(json.dumps({"kind": kind, "data": data}))


def _publish_save_folder(mqttc, request={"action": "", "data": "", "client-id": ""}):
    """Send save folder name to clients.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    request : dict
        Request dictionary sent to the server.
    """
    # reload config from file
    load_config_from_file(mqttc)

    payload = {
        "kind": "save_folder",
        "data": save_folder,
        "action": request["action"],
        "client-id": request["client-id"],
    }
    mqttc.append_payload(json.dumps(payload))


def _publish_config(mqttc, request={"action": "", "data": "", "client-id": ""}):
    """Send configuration to clients.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    request : dict
        Request dictionary sent to the server.
    """
    # reload config from file
    load_config_from_file(mqttc)

    payload = {
        "kind": "config",
        "data": config,
        "action": request["action"],
        "client-id": request["client-id"],
    }
    mqttc.append_payload(json.dumps(payload))


def _update_config(mqttc, new_config):
    """Update configuration file.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    new_config : dict
        New configuration data to write to file.
    """
    # TODO: implement save config
    payload = {"kind": "warning", "data": "set config not implemented"}
    mqttc.append_payload(json.dumps(payload))

    # reload config from file
    load_config_from_file(mqttc)


def _publish_calibration(mqttc, request={"action": "", "data": "", "client-id": ""}):
    """Send calibration data to clients.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    """
    if calibration is {}:
        kind = "warning"
        data = "No calibration data available."
    else:
        kind = "calibration"
        data = calibration

    payload = {
        "kind": kind,
        "data": data,
        "action": request["action"],
        "client-id": request["client-id"],
    }
    mqttc.append_payload(json.dumps(payload))


def get_timestamp():
    """Create a human readable formatted timestamp string.

    Returns
    -------
    timestamp : str
        Formatted timestamp.
    """
    return time.strftime("[%Y-%m-%d]_[%H-%M-%S_%z]")


def _calibrate_eqe(mqttc):
    """Measure the EQE reference photodiode.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher object
        MQTT queue publisher.
    """
    global calibration

    measurement.controller.set_relay("eqe")

    diode = config["experiments"]["eqe"]["calibration_diode"]

    if (c := config["calibration_diodes"][diode]["connection"]) == "external":
        # if externally connected make sure all mux relays are open
        measurement.controller.clear_mux()

        # move to position
        measurement.goto_stage_position(
            config["calibration_diodes"][diode]["position"],
            handler=_handle_stage_data,
            handler_kwargs={"mqttc": mqttc},
        )
    elif c == "internal":
        # connect required relay
        arr_loc = config["calibration_diodes"][diode]["array_location"]
        measurement.controller.set_mux(arr_loc[0], arr_loc[1], arr_loc[2])

        # move to position
        pos = _calculate_pixel_position("eqe", arr_loc[0], arr_loc[1], arr_loc[2])
        measurement.goto_stage_position(
            pos, handler=_handle_stage_data, handler_kwargs={"mqttc": mqttc},
        )
    else:
        raise ValueError(
            f"Photodiode connection mode '{c}' not recognised. Must be "
            + "'internal' or 'external'."
        )

    cal_wls = config["calibration_diodes"][diode]["eqe"]["wls"]
    cal_settings = config["calibratio_diodes"][diode]["eqe_calibration_settings"]

    eqe_calibration = measurement.calibrate_eqe(
        psu_ch1_voltage=config["psu"]["ch1_voltage"],
        psu_ch1_current=cal_settings["ch1_current"],
        psu_ch2_voltage=config["psu"]["ch2_voltage"],
        psu_ch2_current=cal_settings["ch2_current"],
        psu_ch3_voltage=config["psu"]["ch3_voltage"],
        psu_ch3_current=cal_settings["ch3_current"],
        smu_voltage=cal_settings["smu_voltage"],
        start_wl=min(cal_wls),
        end_wl=max(cal_wls),
        num_points=len(cal_wls),
        grating_change_wls=config["monochromator"]["grating_change_wls"],
        filter_change_wls=config["monochromator"]["filter_change_wls"],
        integration_time=cal_settings["time_constant"],
        auto_gain=True,
        auto_gain_method="user",
        handler=None,
        handler_kwargs={},
    )

    calibration["eqe"][diode]["data"] = eqe_calibration
    calibration["eqe"][diode]["timestamp"] = get_timestamp()

    save_calibration_data()

    _publish_calibration(mqttc)


def _calibrate_psu(mqttc, channel):
    """Measure the reference photodiode as a funtcion of LED current."""
    # TODO: complete args for func
    measurement.controller.set_relay("iv")
    measurement.calibrate_psu()


def _calibrate_solarsim(mqttc):
    """Calibrate the solar simulator."""
    # TODO; add calibrate solar sim func
    measurement.controller.set_relay("iv")
    solarsim_spectral_calibration = config["solarsim"]["spectral_calibration"]


def _home(mqttc):
    """Home the stage."""
    measurement.home_stage(config["stage"]["length"])


def _goto(mqttc, position):
    """Go to a stage position."""
    # TODO: complete args for func
    measurement.goto_stage_position()


def _read_stage(mqttc):
    """Read the stage position."""
    # TODO: complete args for func
    measurement.read_stage_position()


def _contact_check(mqttc):
    """Perform contact check."""
    # TODO: write back to gui
    array = config["substrates"]["number"]
    rows = array[0]
    try:
        cols = array[1]
    except IndexError:
        cols = 1
    active_layout = config["substrates"]["active_layout"]
    pcb_adapter = config[active_layout]["pcb_name"]
    pixels = config[pcb_adapter]["pixels"]
    measurement.check_all_contacts(rows, cols, pixels)


def _get_substrate_positions(experiment):
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
    experiment_centre = config["experiment"][experiment]["positions"]

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

    # read in step length in steps/mm
    steplength = config["stage"]["steplength"]

    # get absolute substrate centres along each axis
    axis_pos = []
    for offset, spacing, number, centre in zip(
        substrate_offsets, substrate_spacing, substrate_number, experiment_centre,
    ):
        abs_offset = offset * (spacing / steplength) + centre
        axis_pos.append(np.linspace(-abs_offset, abs_offset, number))

    # create array of positions
    substrate_centres = list(itertools.product(*axis_pos))

    return substrate_centres


def _get_substrate_index(array_loc, array_size):
    """Get the index of a substrate in a flattened array.

    Parameters
    ----------
    array_loc : list of int
        Position of the substrate in the array along each available axis.
    array_size : list of int
        Number of substrates in the array along each available axis.

    Returns
    -------
    index : int
        Index of the substrate in the flattened array.
    """
    if len(array_loc) > 1:
        # get position along last axis
        last_axis_loc = array_loc.pop()

        # pop length of last axis, it's not needed anymore
        array_size.pop()

        # get the total number of substrates in each subarray comprised of remaining
        # axes
        subarray_total = 1
        for n in array_size:
            subarray_total = subarray_total * n

        # get the number of substrates in all subarrays along the last axis up to the
        # level below the substrate location
        subarray_total = subarray_total * (last_axis_loc - 1)

        # recursively iterate through axes, adding smaller subarray totals as axes are
        # reduced to 1
        index = _get_substrate_index(array_loc, array_size) + subarray_total

    return index


def _calculate_pixel_position(experiment, row, col, pixel):
    """Calculate the position of a pixel.

    Parameters
    ----------
    experiment : str
        Experiment centre to move to.
    row : int
        Row index of substrate in the array, 1-indexed.
    pixel_pos : list of float
        Relative pixel centre positoin to centre of the substrate in mm along each
        axis.
    step_length : float
        Length of a single stage step in mm.
    """
    # find the absolute position of the substrate centre
    centres = _get_substrate_positions(experiment)
    array_size = config["substrates"]["number"]
    index = _get_substrate_index([row, col], array_size)
    # because row and col are 1-indexed, so is index, therefore subtract 1
    centre = centres[index - 1]

    # look up the pixel position in mm
    layout = config["substrates"]["active_layout"]
    pos = config["substrates"]["layouts"][layout]["positions"][pixel]

    # convert relative pixel positions in mm to steps
    step_length = config["stage"]["steplength"]
    pos = [x / step_length for x in pos]

    # calculate absolute position of pixel
    pos = [sum(x) for x in zip(pos, centre)]

    return pos


def _build_q(args, pixel_address_string, experiment):
    """Generate a queue of pixels we'll run through.

    Parameters
    ----------
    args : types.SimpleNamespace
        Experiment arguments.
    pixel_address_string : str
        Hexadecimal bitmask string.
    experiment : str
        Name used to look up the experiment centre stage position from the config
        file.

    Returns
    -------
    pixel_q : deque
        Queue of pixels to measure.
    """
    # TODO: return support for inferring layout from pcb adapter resistors

    # get substrate centres
    substrate_centres = _get_substrate_positions(experiment)
    substrate_total = len(substrate_centres)

    # number of substrates along each available axis
    substrate_number = config["substrates"]["number"]

    # number of available axes
    axes = len(substrate_centres[0])

    # make sure as many layouts as labels were given
    if ((l1 := len(args.layouts)) != substrate_total) or (
        (l2 := len(args.labels)) != substrate_total
    ):
        raise ValueError(
            "Lists of layouts and labels must match number of substrates in the "
            + f"array: {substrate_total}. Layouts list has length {l1} and labels list "
            + f"has length {l2}."
        )

    # create a substrate queue where each element is a dictionary of info about the
    # layout from the config file
    substrate_q = []
    i = 0
    for layout, label, centre in zip(args.layouts, args.labels, substrate_centres):
        # get pcb adapter info from config file
        pcb_name = config["substrates"]["layouts"][layout]["pcb_name"]

        # read in pixel positions from layout in config file
        config_pos = config["substrates"]["layouts"][layout]["positions"]
        pixel_positions = []
        for pos in range(len(config_pos)):
            abs_pixel_position = [int(x) for x in zip(pos, centre)]
            pixel_positions.append(abs_pixel_position)

        # find co-ordinate of substrate in the array
        _substrates = np.linspace(1, substrate_total, substrate_total)
        _array = np.reshape(_substrates, substrate_number)
        array_loc = [int(ix) + 1 for ix in np.where(_array == i)]

        substrate_dict = {
            "label": label,
            "array_loc": array_loc,
            "layout": layout,
            "pcb_name": pcb_name,
            "pcb_contact_pads": config[pcb_name]["pcb_contact_pads"],
            "pcb_resistor": config[pcb_name]["pcb_resistor"],
            "pixels": config[layout]["pixels"],
            "pixel_positions": pixel_positions,
            "areas": config[layout]["areas"],
        }
        substrate_q.append(substrate_dict)

        i += 1

    # TODO: return support for pixel strings that aren't hex bitmasks
    # convert hex bitmask string into bit list where 1's and 0's represent whether
    # a pixel should be measured or not, respectively
    bitmask = [int(x) for x in bin(int(pixel_address_string, 16))[2:]]

    # build pixel queue
    pixel_q = collections.deque()
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


def _connect_instruments(mqttc, args):
    """Init fabric object and connect instruments.

    Determine which instruments are connected and their settings from the config
    file.
    """
    if args.dummy is False:
        visa_lib = config["visa"]["visa_lib"]
        smu_address = config["smu"]["address"]
        smu_terminator = config["smu"]["terminator"]
        smu_baud = config["smu"]["baud"]
        light_address = config["solarsim"]["address"]
        controller_address = config["controller"]["address"]
        lia_address = config["lia"]["address"]
        lia_output_interface = config["lia"]["output_interface"]
        mono_address = config["mono"]["address"]
        psu_address = config["psu"]["address"]
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
    measurement.connect(
        dummy=args.dummy,
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
    measurement.sm.setTerminals(front=config["smu"]["front_terminals"])
    measurement.sm.setWires(twoWire=config["smu"]["two_wire"])


def _handle_measurement_data(data, **kwargs):
    """Publish measurement data.

    Parameters
    ----------
    data : list
        List of data to publish.
    **kwargs : dict
        Dictionary of additional keyword arguments required by handler. Should have
        three keys: "kind" whose value is a string indicating the kind of measurement
        data; "idn" whose value is an identity string; and "mqttc" whose value is an
        MQTT queue publisher client.
    """
    kind = kwargs["kind"]
    idn = kwargs["idn"]
    mqttc = kwargs["mqttc"]

    payload = {
        "kind": kind,
        "data": {"data": data, "id": idn, "clear": False, "end": False},
    }
    mqttc.append_payload(json.dumps(payload))


def _handle_stage_data(data, **kwargs):
    """Publish stage position data.

    Parameters
    ----------
    data : list
        List of data to publish.
    **kwargs : dict
        Dictionary of additional keyword arguments required by handler. Should have
        one keys: "mqttc" whose value is an MQTT queue publisher client.
    """
    mqttc = kwargs["mqttc"]

    payload = {"kind": "stage_position", "data": data}
    mqttc.append_payload(json.dumps(payload))


def _handle_save_settings(settings, **kwargs):
    """Publish stage position data.

    Parameters
    ----------
    settings : dict
        Dictionary of save settings.
    **kwargs : dict
        Dictionary of additional keyword arguments required by handler. Should have
        one keys: "mqttc" whose value is an MQTT queue publisher client.
    """
    mqttc = kwargs["mqttc"]

    payload = {"kind": "save_settings", "data": settings}
    mqttc.append_payload(json.dumps(payload))


def _ivt(mqttc, pixel_queue, args):
    """Run through pixel queue of i-v-t measurements.

    Paramters
    ---------
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    pixel_queue : deque of dict
        Queue of dictionaries of pixels to measure.
    args : types.SimpleNamespace
        Experiment arguments.
    """
    # set the master experiment relay
    measurement.controller.set_relay("iv")

    last_label = None
    # scan through the pixels and do the requested measurements
    while len(pixel_queue) > 0:
        pixel = pixel_queue.popleft()
        label = pixel["label"]
        pix = pixel["pixel"]
        print(f"\nOperating on substrate {label}, pixel {pix}...")

        # add id str to handlers to display on plots
        idn = f"{label}_pixel{pix}"

        # we have a new substrate
        if last_label != label:
            print(f"New substrate using '{pixel['layout']}' layout!")
            last_label = label

        # move to pixel
        measurement.goto_stage_position(
            pixel["position"],
            handler=_handle_stage_data,
            handler_kwargs={"mqttc": mqttc},
        )

        # init parameters derived from steadystate measurements
        ssvoc = None

        # get or estimate compliance current
        if type(args.current_compliance_override) == float:
            compliance_i = args.current_compliance_override
        else:
            # estimate compliance current based on area
            compliance_i = measurement.compliance_current_guess(pixel["area"])

        # steady state v@constant I measured here - usually Voc
        if args.v_t > 0:
            # clear v@constant I plot
            mqttc.append_payload(
                {
                    "kind": "vt_measurement",
                    "data": {"id": idn, "clear": True, "end": False},
                }
            )

            vt = measurement.steady_state(
                t_dwell=args.v_t,
                NPLC=args.steadystate_nplc,
                stepDelay=args.steadystate_step_delay,
                sourceVoltage=False,
                compliance=args.voltage_compliance_override,
                senseRange="a",
                setPoint=args.steadystate_i,
                handler=_handle_measurement_data,
                handler_kwargs={"kind": "vt_measurement", "idn": idn, "mqttc": mqttc},
            )

            # signal end of measurement
            mqttc.append_payload(
                {
                    "kind": "vt_measurement",
                    "data": {"id": idn, "clear": False, "end": True},
                }
            )

            # if this was at Voc, use the last measurement as estimate of Voc
            if args.steadystate_i == 0:
                ssvoc = vt[-1]
                measurement.mppt.Voc = ssvoc

        if (args.sweep_1 is True) or (args.sweep_1 is True):
            # clear iv plot
            mqttc.append_payload(
                {
                    "kind": "iv_measurement",
                    "data": {"id": idn, "clear": True, "end": False},
                }
            )

        # TODO: add support for dark measurement, has to use autorange
        if args.sweep_1 is True:
            # determine sweep start voltage
            if type(args.scan_start_override_1) == float:
                start = args.scan_start_override_1
            elif ssvoc is not None:
                start = ssvoc * (1 + (config["iv"]["percent_beyond_voc"] / 100))
            else:
                raise ValueError(
                    f"Start voltage wasn't given and couldn't be inferred."
                )

            # determine sweep end voltage
            if type(args.scan_end_override_1) == float:
                end = args.scan_end_override_1
            else:
                end = -1 * np.sign(ssvoc) * config["iv"]["voltage_beyond_isc"]

            print(f"Sweeping voltage from {start} V to {end} V")

            iv1 = measurement.sweep(
                sourceVoltage=True,
                compliance=compliance_i,
                senseRange="f",
                nPoints=args.scan_points,
                stepDelay=args.scan_step_delay,
                start=start,
                end=end,
                NPLC=args.scan_nplc,
                handler=_handle_measurement_data,
                handler_kwargs={"kind": "iv_measurement", "idn": idn, "mqttc": mqttc},
            )

            Pmax_sweep1, Vmpp1, Impp1, maxIx1 = measurement.mppt.which_max_power(iv1)

        if args.sweep_2 is True:
            # sweep the opposite way to sweep 1
            start = end
            end = start

            print(f"Sweeping voltage from {start} V to {end} V")

            iv2 = measurement.sweep(
                sourceVoltage=True,
                senseRange="f",
                compliance=compliance_i,
                nPoints=args.scan_points,
                start=start,
                end=end,
                NPLC=args.scan_nplc,
                handler=_handle_measurement_data,
                handler_kwargs={"kind": "iv_measurement", "idn": idn, "mqttc": mqttc},
            )

            Pmax_sweep2, Vmpp2, Impp2, maxIx2 = measurement.mppt.which_max_power(iv2)

        if (args.sweep_1 is True) or (args.sweep_1 is True):
            # signal end of iv measurements
            mqttc.append_payload(
                {
                    "kind": "iv_measurement",
                    "data": {"id": idn, "clear": False, "end": True},
                }
            )

        # TODO: read and interpret parameters for smart mode
        # # determine Vmpp and current compliance for mppt
        # if (self.args.sweep_1 is True) & (self.args.sweep_2 is True):
        #     if abs(Pmax_sweep1) > abs(Pmax_sweep2):
        #         Vmpp = Vmpp1
        #         compliance_i = Impp1 * 5
        #     else:
        #         Vmpp = Vmpp2
        #         compliance_i = Impp2 * 5
        # elif self.args.sweep_1 is True:
        #     Vmpp = Vmpp1
        #     compliance_i = Impp1 * 5
        # else:
        #     # no sweeps have been measured so max power tracker will estimate Vmpp
        #     # based on Voc (or measure it if also no Voc) and will use initial
        #     # compliance set before any measurements were taken.
        #     Vmpp = None
        # self.logic.mppt.Vmpp = Vmpp
        measurement.mppt.current_compliance = compliance_i

        if args.mppt_t > 0:
            print(f"Tracking maximum power point for {args.mppt_t} seconds.")

            # clear mppt plot
            mqttc.append_payload(
                {
                    "kind": "mppt_measurement",
                    "data": {"id": idn, "clear": True, "end": False},
                }
            )

            # measure voc for 1s to initialise mppt
            vt = measurement.steady_state(
                t_dwell=1,
                NPLC=args.steadystate_nplc,
                stepDelay=args.steadystate_step_delay,
                sourceVoltage=False,
                compliance=args.voltage_compliance_override,
                senseRange="a",
                setPoint=0,
                handler=_handle_measurement_data,
                handler_kwargs={"kind": "mppt_measurement", "idn": idn, "mqttc": mqttc},
            )
            measurement.mppt.Voc = vt[-1]

            mt = measurement.track_max_power(
                args.mppt_t,
                NPLC=args.steadystate_nplc,
                stepDelay=args.steadystate_step_delay,
                extra=args.mppt_params,
                handler=_handle_measurement_data,
                handler_kwargs={"kind": "mppt_measurement", "idn": idn, "mqttc": mqttc},
            )

            # signal end of measurement
            mqttc.append_payload(
                {
                    "kind": "mppt_measurement",
                    "data": {"id": idn, "clear": False, "end": True},
                }
            )

        if args.i_t > 0:
            # steady state I@constant V measured here - usually Isc
            # clear I@constant V plot
            mqttc.append_payload(
                {
                    "kind": "it_measurement",
                    "data": {"id": idn, "clear": True, "end": False},
                }
            )

            it = measurement.steady_state(
                t_dwell=args.i_t,
                NPLC=args.steadystate_nplc,
                stepDelay=args.steadystate_step_delay,
                sourceVoltage=True,
                compliance=compliance_i,
                senseRange="a",
                setPoint=args.steadystate_v,
                handler=_handle_measurement_data,
                handler_kwargs={"kind": "it_measurement", "idn": idn, "mqttc": mqttc},
            )

            # signal end of measurement
            mqttc.append_payload(
                {
                    "kind": "it_measurement",
                    "data": {"id": idn, "clear": False, "end": True},
                }
            )

    measurement.run_done()


def _eqe(mqttc, pixel_queue, args):
    """Run through pixel queue of EQE measurements.

    Paramters
    ---------
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    pixel_queue : deque of dict
        Queue of dictionaries of pixels to measure.
    args : types.SimpleNamespace
        Experiment arguments.
    """
    measurement.controller.set_relay("eqe")

    while len(pixel_queue) > 0:
        pixel = pixel_queue.popleft()
        label = pixel["label"]
        pix = pixel["pixel"]
        print(f"\nOperating on substrate {label}, pixel {pix}...")

        # add id str to handlers to display on plots
        idn = f"{label}_pixel{pix}"

        # we have a new substrate
        if last_label != label:
            print(f"New substrate using '{pixel['layout']}' layout!")
            last_label = label

        # move to pixel
        measurement.goto_stage_position(
            pixel["position"],
            handler=_handle_stage_data,
            handler_kwargs={"mqttc": mqttc},
        )

        print(f"Scanning EQE from {args.eqe_start_wl} nm to {args.eqe_end_wl} nm")

        # clear eqe plot
        mqttc.append_payload(
            {
                "kind": "eqe_measurement",
                "data": {"id": idn, "clear": True, "end": False},
            }
        )

        diode = config["experiments"]["eqe"]["calibration_photodiode"]

        # TODO: fill in paths
        measurement.eqe(
            psu_ch1_voltage=config["psu"]["ch1_voltage"],
            psu_ch1_current=args.psu_is[0],
            psu_ch2_voltage=config["psu"]["ch2_voltage"],
            psu_ch2_current=args.psu_is[1],
            psu_ch3_voltage=config["psu"]["ch3_voltage"],
            psu_ch3_current=args.psu_is[2],
            smu_voltage=args.eqe_smu_v,
            ref_measurement=calibration["eqe"][diode],
            ref_eqe=config["calibration_photodiodes"][diode]["eqe"],
            ref_spectrum=config["reference"]["spectra"]["AM1.5G"],
            start_wl=args.eqe_start_wl,
            end_wl=args.eqe_end_wl,
            num_points=args.eqe_num_wls,
            grating_change_wls=config["monochromator"]["grating_change_wls"],
            filter_change_wls=config["monochromator"]["filter_change_wls"],
            auto_gain=not (args.eqe_autogain_off),
            auto_gain_method=args.eqe_autogain_method,
            integration_time=args.eqe_integration_time,
            handler=_handle_measurement_data,
            handler_kwargs={"kind": "eqe_measurement", "idn": idn, "mqttc": mqttc},
        )

        # signal end of measurement
        mqttc.append_payload(
            {
                "kind": "eqe_measurement",
                "data": {"id": idn, "clear": False, "end": True},
            }
        )


def _test_hardware(mqttc, config):
    """Test hardware."""
    # TODO: fill in func
    pass


def _run(mqttc, args):
    """Act on command line instructions.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client for sending responses.
    args : types.SimpleNamespace
        Arguments required to run a measurement.
    """
    # the run function only runs in a separate thread so make sure all mutations of
    # save_folder are atomic to ensure thread safety
    global save_folder

    # update save folder and publish it
    save_folder = args.destination
    _publish_save_folder(mqttc)

    # publish other settings
    _publish_config(mqttc)
    _publish_calibration(mqttc)

    # TODO: make funcs
    _publish_args(mqttc, args)

    # connect all instruments
    measurement.connect_all_instruments(
        args.dummy,
        config["visa"]["visa_lib"],
        config["smu"]["smus"]["smu1"]["address"],
        config["smu"]["terminator"],
        config["smu"]["baud"],
        config["controller"]["address"],
        config["solarsimulator"]["address"],
        config["lia"]["address"],
        config["lia"]["terminator"],
        config["lia"]["baud"],
        config["lia"]["output_interface"],
        config["monochromator"]["address"],
        config["monochromator"]["terminator"],
        config["monochromator"]["baud"],
        config["psu"]["address"],
        config["psu"]["terminator"],
        config["psu"]["baud"],
    )

    # build up the queue of pixels to run through
    if args.dummy is True:
        args.iv_pixel_address = "0x1"
        args.eqe_pixel_address = "0x1"

    if args.iv_pixel_address is not None:
        iv_pixel_queue = _build_q(args, args.iv_pixel_address, experiment="solarsim")
    else:
        iv_pixel_queue = []

    if args.eqe_pixel_address is not None:
        eqe_pixel_queue = _build_q(args, args.eqe_pixel_address, experiment="eqe")
    else:
        eqe_pixel_queue = []

    # measure i-v-t
    if len(iv_pixel_queue) > 0:
        _ivt(mqttc, iv_pixel_queue, args)

    # measure eqe
    if len(eqe_pixel_queue) > 0:
        _eqe(mqttc, eqe_pixel_queue, args)

    # disconnect all instruments
    measurement.disconnect_all_instruments()


def on_message(mqttc, obj, msg):
    """Act on an MQTT message.

    Actions that require instrument I/O run in a worker thread. Only one action thread
    can run at a time. If an action thread is running the server will report that it's
    busy.
    """
    request = json.loads(msg.payload)
    action = request["action"]
    data = request["data"]

    # perform a requested action
    if action == "get_config":
        # respond immediately
        _publish_config(mqttc, request)
    elif action == "set_config":
        # can't set config while action is being performed
        start_thread(mqttc, _update_config, (mqttc, data,), action)
    elif action == "get_calibration":
        # respond immediately
        _publish_calibration(mqttc, request)
    elif action == "get_save_folder":
        # respond immediately
        _publish_save_folder(mqttc, request)
    elif action == "run":
        args = types.SimpleNamespace(**data)
        start_thread(mqttc, _run, (mqttc, args,), action)
    elif action == "stop":
        # kill the server, external process will restart it
        sys.exit(1)
    elif action == "calibrate_solarsim":
        start_thread(mqttc, _calibrate_solarsim, (mqttc,), action)
    elif action == "calibrate_eqe":
        start_thread(mqttc, _calibrate_eqe, (mqttc,), action)
    elif action == "calibrate_psu":
        start_thread(mqttc, _calibrate_psu, (mqttc, data,), action)
    elif action == "home":
        start_thread(mqttc, _home, (mqttc,), action)
    elif action == "goto":
        start_thread(mqttc, _goto, (mqttc, data,), action)
    elif action == "read_stage":
        start_thread(mqttc, _read_stage, (mqttc,), action)


# required when using multiprocessing in windows, advised on other platforms
if __name__ == "__main__":
    cli_args = get_args()

    # create dummy thread
    thread = threading.Thread()

    # create fabric measurement logic object
    measurement = central_control.fabric.fabric()

    with MQTTQueuePublisher() as mqtt_server:
        mqtt_server.on_message = on_message
        # connect MQTT client to broker
        mqtt_server.connect(cli_args.MQTTHOST)
        # subscribe to everything in the server/request topic
        mqtt_server.subscribe("server/request")
        # start publisher queue for processing responses
        mqtt_server.start_q("server/response")

        # load config file
        config = {}
        load_config_from_file(mqtt_server)

        # try to laod calibration data and let clients know if none available
        calibration = {}
        load_calibration_from_file(mqtt_server)

        # forget save folder, a new one will be required
        save_folder = None

        mqtt_server.loop_forever()
