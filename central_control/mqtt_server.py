"""Client for running the CLI based on MQTT messages."""

import collections
import json
import multiprocessing as mp
import types
import warnings

from mqtt_tools.queue_publisher import MQTTQueuePublisher

import central_control.fabric


# create dummy process
process = mp.Process()

# create dummy config
config = {}

# create measutrement object
measurement = central_control.fabric.fabric()


def start_process(target, args=()):
    """Run a function in a new process.

    Parameters
    ----------
    target : function
        Name of function to run in a new process.
    args : tuple
        Arguments to pass to the function formatted as (arg1, arg2, ...,).
    """
    global process

    # try to start a new process. Ignore request if a process is still running.
    if process.is_alive() is False:
        process = mp.Process(target=target, args=args)
        process.start()
        print(f"Started process with PID={process.pid}!")
    else:
        print(f"Cannot start new process. A process is still running with PID={process.pid}.")


def stop_process():
    """Stop an active process running a requested function."""
    global process

    # if the process is still alive, stop it
    if process.is_alive() is True:
        process.terminate()
        process.join()
        print(f"Stopped process with PID={process.pid}.")
    else:
        print(f"Process with PID={process.pid} has already stopped.")


def save_config():
    """Send configuration to save clients."""
    # TODO: fill in func
    pass


def calibrate_eqe(args):
    """Measure the EQE reference photodiode."""
    global measurement
    # TODO: complete args for func
    measurement.calibrate_eqe()


def calibrate_psu(args):
    """Measure the reference photodiode as a funtcion of LED current."""
    global measurement
    # TODO: complete args for func
    measurement.calibrate_psu()


def calibrate_solarsim(args):
    """Calibrate the solar simulator."""
    # TODO; add calibrate solar sim func
    measurement.controller.set_relay("iv")
    solarsim_spectral_calibration = config["solarsim"]["spectral_calibration"]


def home():
    """Home the stage."""
    global measurement
    measurement.home_stage(config["stage"]["length"])


def goto(args):
    """Go to a stage position."""
    global measurement
    # TODO: complete args for func
    measurement.goto_stage_position()


def read_stage():
    """Read the stage position."""
    global measurement
    # TODO: complete args for func
    measurement.read_stage_position()


def contact_check():
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


def verify_save_client():
    """Verify the MQTT client for saving data is running."""
    # TODO: at verification method.
    pass


def get_substrate_positions(experiment):
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
    experiment_centre = config["experiment_positions"][experiment]

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
        substrate_offsets,
        substrate_spacing,
        substrate_number,
        experiment_centre,
    ):
        abs_offset = offset * (spacing / steplength) + centre
        axis_pos.append(np.linspace(-abs_offset, abs_offset, number))

    # create array of positions
    substrate_centres = list(itertools.product(*axis_pos))

    return substrate_centres


def build_q(args, pixel_address_string, experiment):
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
    substrate_centres = get_substrate_positions(experiment)
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
            f"Lists of layouts and labels must match number of substrates in the array: {substrate_total}. Layouts list has length {l1} and labels list has length {l2}."
        )

    # create a substrate queue where each element is a dictionary of info about the
    # layout from the config file
    substrate_q = []
    i = 0
    for layout, label, centre in zip(
        args.layouts, args.labels, substrate_centres
    ):
        # get pcb adapter info from config file
        pcb_name = config[layout]["pcb_name"]

        # read in pixel positions from layout in config file
        config_pos = config[layout]["positions"]
        pixel_positions = []
        for i in range(0, len(config_pos), axes):
            abs_pixel_position = [
                int(x + y) for x, y in zip(config_pos[i : i + axes], centre)
            ]
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

def _connect_instruments(args):
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
    measurement.sm.setTerminals(
        front=config["smu"]["front_terminals"]
    )
    measurement.sm.setWires(twoWire=config["smu"]["two_wire"])


def handle_measurement_data(data, **kwargs):
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
        "data": {
            "data": data,
            "id": idn,
            "clear": False,
            "end": False,
        }
    }
    mqtt.append_payload(json.dumps(payload))


def handle_stage_data(data, **kwargs):
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
    mqtt.append_payload(json.dumps(payload))


def handle_save_settings(settings, **kwargs):
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
    mqtt.append_payload(json.dumps(payload))


def ivt(mqttc, pixel_queue, args):
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
    global measurement

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
            handler=handle_stage_data,
            handler_kwargs={"mqttc": mqttc},
        )

        # init parameters derived from steadystate measurements
        ssvoc = None
        ssisc = None

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
                    "data": {
                        "id": idn, "clear": True, "end": False
                        }
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
                handler=handle_measurement_data,
                handler_kwargs={"kind": "vt_measurement", "idn": idn, "mqttc": mqttc},
            )

            # signal end of measurement
            mqttc.append_payload(
                {
                    "kind": "vt_measurement",
                    "data": {
                        "id": idn, "clear": False, "end": True
                        }
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
                    "data": {
                        "id": idn, "clear": True, "end": False
                        }
                }
            )

        # TODO: add support for dark measurement, has to use autorange
        if args.sweep_1 is True:
            # determine sweep start voltage
            if type(args.scan_start_override_1) == float:
                start = args.scan_start_override_1
            elif ssvoc is not None:
                start = ssvoc * (
                    1 + (config["iv"]["percent_beyond_voc"] / 100)
                )
            else:
                raise ValueError(
                    f"Start voltage wasn't given and couldn't be inferred."
                )

            # determine sweep end voltage
            if type(args.scan_end_override_1) == float:
                end = args.scan_end_override_1
            else:
                end = (
                    -1
                    * np.sign(ssvoc)
                    * config["iv"]["voltage_beyond_isc"]
                )

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
                handler=handle_measurement_data,
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
                handler=handle_measurement_data,
                handler_kwargs={"kind": "iv_measurement", "idn": idn, "mqttc": mqttc},
            )

            Pmax_sweep2, Vmpp2, Impp2, maxIx2 = measurement.mppt.which_max_power(iv2)

        if (args.sweep_1 is True) or (args.sweep_1 is True):
            # signal end of iv measurements
            mqttc.append_payload(
                {
                    "kind": "iv_measurement",
                    "data": {
                        "id": idn, "clear": False, "end": True
                        }
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
                    "data": {
                        "id": idn, "clear": True, "end": False
                        }
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
                handler=handle_measurement_data,
                handler_kwargs={"kind": "mppt_measurement", "idn": idn, "mqttc": mqttc},
            )
            measurement.mppt.Voc = vt[-1]

            mt = measurement.track_max_power(
                args.mppt_t,
                NPLC=args.steadystate_nplc,
                stepDelay=args.steadystate_step_delay,
                extra=args.mppt_params,
                handler=handle_measurement_data,
                handler_kwargs={"kind": "mppt_measurement", "idn": idn, "mqttc": mqttc},
            )

            # signal end of measurement
            mqttc.append_payload(
                {
                    "kind": "mppt_measurement",
                    "data": {
                        "id": idn, "clear": False, "end": True
                        }
                }
            )

        if args.i_t > 0:
            # steady state I@constant V measured here - usually Isc
            # clear I@constant V plot
            mqttc.append_payload(
                {
                    "kind": "it_measurement",
                    "data": {
                        "id": idn, "clear": True, "end": False
                        }
                }
            )

            it = measurement.logic.steady_state(
                t_dwell=args.i_t,
                NPLC=args.steadystate_nplc,
                stepDelay=args.steadystate_step_delay,
                sourceVoltage=True,
                compliance=compliance_i,
                senseRange="a",
                setPoint=args.steadystate_v,
                handler=handle_measurement_data,
                handler_kwargs={"kind": "it_measurement", "idn": idn, "mqttc": mqttc},
            )

            # signal end of measurement
            mqttc.append_payload(
                {
                    "kind": "it_measurement",
                    "data": {
                        "id": idn, "clear": False, "end": True
                        }
                }
            )

    measurement.run_done()


def eqe(mqqtc, pixel_queue, args):
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
    global measurement
    measurement.controller.set_relay("eqe")

    # look up settings from config
    grating_change_wls = config["monochromator"]["grating_change_wls"]
    filter_change_wls = config["monochromator"]["filter_change_wls"]

    while len(pixel_queue) > 0:
        pixel = pixel_queue.popleft()
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
        measurement.goto_stage_position(
            pixel["position"],
            handler=handle_stage_data,
            handler_kwargs={"mqttc": mqttc},
        )

        print(
            f"Scanning EQE from {args.eqe_start_wl} nm to {args.eqe_end_wl} nm"
        )

        # clear eqe plot
        mqttc.append_payload(
            {
                "kind": "eqe_measurement",
                "data": {
                    "id": idn, "clear": True, "end": False
                    }
            }
        )

        # TODO: fill in paths
        measurement.eqe(
            psu_ch1_voltage=config["psu"]["ch1_voltage"],
            psu_ch1_current=args.psu_is[0],
            psu_ch2_voltage=config["psu"]["ch2_voltage"],
            psu_ch2_current=args.psu_is[1],
            psu_ch3_voltage=config["psu"]["ch3_voltage"],
            psu_ch3_current=args.psu_is[2],
            smu_voltage=args.eqe_smu_v,
            calibration=False,
            ref_measurement_path=,
            ref_measurement_file_header=1,
            ref_eqe_path=,
            ref_spectrum_path=,
            start_wl=args.eqe_start_wl,
            end_wl=args.eqe_end_wl,
            num_points=args.eqe_num_wls,
            repeats=args.eqe_repeats,
            grating_change_wls=config["monochromator"]["grating_change_wls"],
            filter_change_wls=config["monochromator"]["filter_change_wls"],
            auto_gain=not (args.eqe_autogain_off),
            auto_gain_method=args.eqe_autogain_method,
            integration_time=args.eqe_integration_time,
            handler=handle_measurement_data,
            handler_kwargs={"kind": "eqe_measurement", "idn": idn, "mqttc": mqttc},
        )

        # signal end of measurement
        mqttc.append_payload(
            {
                "kind": "eqe_measurement",
                "data": {
                    "id": idn, "clear": False, "end": True
                    }
            }
        )


def test_hardware():
    """Test hardware."""
    # TODO: fill in func
    pass


def run(mqttc, args):
    """Act on command line instructions.

    Parameters
    ----------
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client for sending responses.
    args : types.SimpleNamespace
        Arguments required to run a measurement.
    """
    global measurement

    # verify a save client is available
    verify_save_client()

    # report all settings
    # TODO: make funcs
    save_args(mqttc, args)
    save_settings()

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
        iv_pixel_queue = build_q(
            args.iv_pixel_address, experiment="solarsim"
        )
    else:
        iv_pixel_queue = []

    if args.eqe_pixel_address is not None:
        eqe_pixel_queue = build_q(
            args.eqe_pixel_address, experiment="eqe"
        )
    else:
        eqe_pixel_queue = []

    # measure i-v-t
    if len(iv_pixel_queue) > 0:
        ivt(mqttc, iv_pixel_queue, args)

    # measure eqe
    if len(eqe_pixel_queue) > 0:
        eqe(mqqtc, eqe_pixel_queue, args)

    # disconnect all instruments
    measurement.disconnect_all_instruments()


def on_message(mqttc, obj, msg):
    """Act on an MQTT message."""
    m = json.loads(msg.payload)
    action = m["action"]
    data = m["data"]

    # perform action depending on which button generated the message
    if action == "get_config":
        start_process(get_config)
    elif action == "set_config":
        start_process(set_config)
    elif action == "run":
        args = types.SimpleNamespace(**data)
        start_process(run, args)
    elif action == "stop":
        stop_process()
    elif action == "calibrate_solarsim":
        start_process(calibrate_solarsim, data)
    elif action == "calibrate_eqe":
        start_process(calibrate_eqe, data)
    elif action == "calibrate_psu":
        start_process(calibrate_psu, data)
    elif action == "home":
        start_process(home)
    elif action == "goto":
        start_process(goto, data)
    elif action == "read_stage":
        start_process(read_stage)


# required when using multiprocessing in windows, advised on other platforms
if __name__ == "__main__":
    import argparse

    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mqtthost",
        default="127.0.0.1",
        help="IP address or hostname of MQTT broker.",
    )
    args = parser.parse_args()

    # try to load the configuration file from the current working directory
    try:
        with open("measurement_config.yaml", "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
    except FileNotFoundError:
        # maybe running a test from project directory
        try:
            with open("example_config.yaml", "r") as f:
                config = yaml.load(f, Loader=yaml.FullLoader)
            print(f"'measurement_config.yaml' not found in current working directory: {os.getcwd()}. Falling back on 'example_config.yaml' project file.")
        except FileNotFoundError:
            raise FileNotFoundError(f"No configuration file could be found in current working directory: {os.getcwd()}. Please run the server from a directory with a valid 'measurement_config.yaml' file.")

    with MQTTQueuePublisher() as mqtt_server:
        mqtt_server.on_message = on_message
        # connect MQTT client to broker
        mqtt_server.connect(args.MQTTHOST)
        # subscribe to everything in the server/request topic
        mqtt_server.subscribe("server/request")
        # start publisher queue for processing responses
        mqtt_server.start_q("server/response")
        mqtt_server.loop_forever()
