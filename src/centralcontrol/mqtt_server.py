#!/usr/bin/env python3
"""MQTT Server for interacting with the system."""

from pathlib import Path
import sys
import argparse
import collections
import multiprocessing
import os
import pickle
import queue
import signal
import time
import traceback
import uuid

import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish

from mqtt_tools.queue_publisher import MQTTQueuePublisher

# this boilerplate code allows this module to be run directly as a script
if (__name__ == "__main__") and (__package__ in [None, ""]):
    __package__ = "centralcontrol"
    # get the dir that holds __package__ on the front of the search path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .fabric import fabric


def get_args():
    """Get arguments parsed from the command line."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mqtthost",
        default="127.0.0.1",
        const="127.0.0.1",
        nargs="?",
        help="IP address or hostname of MQTT broker.",
    )
    return parser.parse_args()


def start_process(cli_args, process, target, args):
    """Start a new process to perform an action if no process is running.

    Parameters
    ----------
    target : function handle
        Function to run in child process.
    args : tuple
        Arguments required by the function.
    """
    if process.is_alive() is False:
        ret_proc = multiprocessing.Process(target=target, args=args)
        ret_proc.start()
        publish.single(
            "measurement/status",
            pickle.dumps("Busy"),
            qos=2,
            retain=True,
            hostname=cli_args.mqtthost,
        )
    else:
        ret_proc = process
        payload = {"level": 30, "msg": "Measurement server busy!"}
        publish.single(
            "measurement/log", pickle.dumps(payload), qos=2, hostname=cli_args.mqtthost
        )

    return ret_proc


def stop_process(cli_args, process):
    """Stop a running process."""
    if process.is_alive() is True:
        os.kill(process.pid, signal.SIGINT)
        process.join()
        print(f"Process still alive?: {process.is_alive()}")
        payload = {"level": 20, "msg": "Request to stop completed!"}
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
        payload = {"level": 30, "msg": "Nothing to stop. Measurement server is idle."}
        publish.single(
            "measurement/log", pickle.dumps(payload), qos=2, hostname=cli_args.mqtthost
        )
    return process


def _calibrate_spectrum(request, mqtthost):
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

    user_aborted = False

    with MQTTQueuePublisher() as mqttc:
        mqttc.connect(mqtthost)
        mqttc.loop_start()

        try:
            with fabric() as measurement:
                measurement.current_limit = request["config"]["smu"]["current_limit"]

                _log("Calibrating solar simulator spectrum...", 20, mqttc)

                config = request["config"]
                args = request["args"]

                measurement.connect_instruments(
                    light_address=config["solarsim"]["address"],
                    light_virt=config["solarsim"]["virtual"],
                    light_recipe=args["light_recipe"],
                )
                if hasattr(measurement, "le"):
                    measurement.le.set_intensity(int(args["light_recipe_int"]))

                timestamp = time.time()

                spectrum = measurement.measure_spectrum()

                spectrum_dict = {"data": spectrum, "timestamp": timestamp}

                # publish calibration
                mqttc.append_payload(
                    "calibration/spectrum", pickle.dumps(spectrum_dict), retain=True
                )

                _log("Finished calibrating solar simulator spectrum!", 20, mqttc)

            print("Spectrum calibration complete.")
        except KeyboardInterrupt:
            user_aborted = True
        except Exception as e:
            traceback.print_exc()
            _log(f"SPECTRUM CALIBRATION ABORTED! " + str(e), 40, mqttc)

        mqttc.append_payload("measurement/status", pickle.dumps("Ready"), retain=True)

    return user_aborted


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

    if experiment == "solarsim":
        stuff = args["IV_stuff"]
    elif experiment == "eqe":
        stuff = args["EQE_stuff"]
    else:
        raise (ValueError(f"Unknown experiment: {experiment}"))
    center = config["stage"]["experiment_positions"][experiment]

    # build pixel dictionary
    pixel_d = {}
    # here we build up the pixel handling queue by iterating
    # through the rows of a pandas dataframe
    # that contains one row for each turned on pixel
    for things in stuff.to_dict(orient="records"):
        pixel_dict = {}
        pixel_dict["label"] = things["label"]
        pixel_dict["layout"] = things["layout"]
        pixel_dict["sub_name"] = things["system_label"]
        pixel_dict["pixel"] = things["mux_index"]
        loc = things["loc"]
        pos = [a + b for a, b in zip(center, loc)]
        pixel_dict["pos"] = pos
        if things["area"] == -1:  # handle custom area
            pixel_dict["area"] = args["a_ovr_spin"]
        else:
            pixel_dict["area"] = things["area"]
        pixel_dict["mux_string"] = things["mux_string"]
        mapping = [x.lower() for x in config["smu"]["channel_mapping"]]
        smu_chan = mapping.index(things["sort_string"].lower())
        pixel_d[smu_chan] = pixel_dict
    return pixel_d


class DataHandler:
    """Handler for measurement data."""

    def __init__(self, kind="", pixels={}, sweep="", mqttqp=None):
        """Construct data handler object.

        Parameters
        ----------
        kind : str
            Kind of measurement data. This is used as a sub-channel name.
        pixels : dict
            Information about all pixels. Keys are SMU channel numbers.
        sweep : {"", "dark", "light"}
            If the handler is for sweep data, specify whether the sweep is under
            "light" or "dark" conditions.
        mqttqp : MQTTQueuePublisher
            MQTT queue publisher object that publishes measurement data.
        """
        self.kind = kind
        self.pixels = pixels
        self.sweep = sweep
        self.mqttqp = mqttqp

    def handle_data(self, data):
        """Handle measurement data.

        Parameters
        ----------
        data : dict of array-like
            Measurement data dictionary. Keys are SMU channel numbers.
        """
        for channel, ch_data in sorted(data.items()):
            try:
                payload = {
                    "data": ch_data,
                    "pixel": self.pixels[channel],
                    "sweep": self.sweep,
                }
                self.mqttqp.append_payload(
                    f"data/raw/{self.kind}", pickle.dumps(payload)
                )
            except KeyError:
                # the data measured for the pixel wasn't requested, i.e.
                # self.pixels[channel] doesn't exist
                pass


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


def _ivt(pixels, request, measurement, mqttc):
    """Run through pixel queue of i-v-t measurements.

    Paramters
    ---------
    pixels : dict
        Pixel information dictionary. Keys are SMU channel numbers.
    request : dict
        Experiment arguments.
    measurement : measurement logic object
        Object controlling instruments and measurements.
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    """
    config = request["config"]
    args = request["args"]

    if args["enable_solarsim"] is True:
        light_address = config["solarsim"]["address"]
    else:
        light_address = None

    # connect instruments
    measurement.connect_instruments(
        smu_address=config["smu"]["address"],
        smu_port=config["smu"]["port"],
        smu_terminator=config["smu"]["terminator"],
        smu_plf=config["smu"]["plf"],
        smu_two_wire=config["smu"]["two_wire"],
        smu_invert_channels=args["inverted_conn"],
        light_address=light_address,
        light_virt=config["solarsim"]["virtual"],
        light_recipe=args["light_recipe"],
    )
    measurement._mqttc = mqttc

    if hasattr(measurement, "le"):
        measurement.le.set_intensity(int(args["light_recipe_int"]))

    # scale smu settling delay
    settling_delay = args["source_delay"] / 1000

    # start daq
    mqttc.append_payload("daq/start", pickle.dumps(""))

    ld = collections.deque([f"{x[1]['label']} Device {x[1]['pixel']}" for x in pixels.items()])  # labels of live devices
    mqttc.append_payload("plotter/live_devices", pickle.dumps(list(ld)))

    # loop over repeats
    loop = 0
    while (loop < args["cycles"]) or (args["cycles"] == 0):
        loop += 1
        # init parameters derived from steadystate measurements
        ssvocs = None

        # get or estimate compliance current
        compliance_i = measurement.compliance_current_guess(
            area=list(pixels.values())[0]["area"], jmax=args["jmax"], imax=args["imax"]
        )
        measurement.mppt.current_compliance = compliance_i

        # setup data handler
        dh = DataHandler(pixels=pixels, mqttqp=mqttc)
        handler = dh.handle_data

        # "Voc" if
        if args["i_dwell"] > 0:
            _log("Measuring steady-state Voc", 20, mqttc)
            # Voc needs light
            if hasattr(measurement, "le"):
                measurement.le.on()

            kind = "vt_measurement"
            dh.kind = kind
            _clear_plot(kind, mqttc)

            # constant current dwell step
            vt = measurement.steady_state(
                t_dwell=args["i_dwell"],
                nplc=args["nplc"],
                settling_delay=settling_delay,
                source_voltage=False,
                set_point=args["i_dwell_value"],
                pixels=pixels,
                handler=handler,
            )

            # if this was at Voc, use the last measurement as estimate of Voc
            if args["i_dwell_value"] == 0:
                ssvocs = {}
                for ch, ch_data in sorted(vt.items()):
                    ssvocs[ch] = ch_data[-1][0]

        # if performing sweeps
        sweeps = []
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

        # perform sweeps
        for sweep in sweeps:
            # sweeps may or may not need light
            if sweep == "dark":
                if hasattr(measurement, "le"):
                    measurement.le.off()
            else:
                if hasattr(measurement, "le"):
                    measurement.le.on()

            if args["sweep_check"] is True:
                _log(f"Performing first {sweep} sweep.", 20, mqttc)
                print(
                    f'Sweeping voltage from {args["sweep_start"]} V to '
                    + f'{args["sweep_end"]} V'
                )

                kind = "iv_measurement/1"
                dh.kind = kind
                dh.sweep = sweep
                _clear_plot("iv_measurement", mqttc)

                iv1 = measurement.sweep(
                    nplc=args["nplc"],
                    settling_delay=settling_delay,
                    start=args["sweep_start"],
                    end=args["sweep_end"],
                    points=int(args["iv_steps"]),
                    source_voltage=True,
                    smart_compliance=config["smu"]["smart_compliance"],
                    pixels=pixels,
                    handler=handler,
                )

                (Pmax_sweep1, Vmpp1, Impp1, maxIx1,) = measurement.mppt.register_curve(
                    iv1, light=(sweep == "light")
                )

            if args["return_switch"] is True:
                _log(f"Performing second {sweep} sweep.", 20, mqttc)
                print(
                    f'Sweeping voltage from {args["sweep_end"]} V to '
                    + f'{args["sweep_start"]} V'
                )

                kind = "iv_measurement/2"
                dh.kind = kind
                dh.sweep = sweep

                iv2 = measurement.sweep(
                    nplc=args["nplc"],
                    settling_delay=settling_delay,
                    start=args["sweep_end"],
                    end=args["sweep_start"],
                    points=int(args["iv_steps"]),
                    source_voltage=True,
                    smart_compliance=config["smu"]["smart_compliance"],
                    pixels=pixels,
                    handler=handler,
                )

                (Pmax_sweep2, Vmpp2, Impp2, maxIx2,) = measurement.mppt.register_curve(
                    iv2, light=(sweep == "light")
                )

        # mppt if
        if args["mppt_dwell"] > 0:
            # mppt needs light
            if hasattr(measurement, "le"):
                measurement.le.on()
            _log(f"Performing max. power tracking.", 20, mqttc)
            print(f"Tracking maximum power point for {args['mppt_dwell']} seconds.")

            kind = "mppt_measurement"
            dh.kind = kind
            _clear_plot(kind, mqttc)

            if ssvocs is not None:
                # tell the mppt what our measured steady state Voc was
                measurement.mppt.Voc = ssvocs

            (mt, vt) = measurement.track_max_power(
                args["mppt_dwell"],
                NPLC=args["nplc"],
                extra=args["mppt_params"],
                voc_compliance=config["ccd"]["max_voltage"],
                i_limit=compliance_i,
                pixels=pixels,
                handler=handler,
            )

            if len(vt) > 0:
                dh.kind = "vtmppt_measurement"
                for d in vt:
                    handler(d)

        # "J_sc" if
        if args["v_dwell"] > 0:
            # jsc needs light
            if hasattr(measurement, "le"):
                measurement.le.on()
            _log(f"Measuring current at constant voltage.", 20, mqttc)

            kind = "it_measurement"
            dh.kind = kind
            _clear_plot(kind, mqttc)

            it = measurement.steady_state(
                t_dwell=args["v_dwell"],
                nplc=args["nplc"],
                settling_delay=settling_delay,
                source_voltage=True,
                set_point=args["v_dwell_value"],
                pixels=pixels,
                handler=handler,
            )

    # update live devices list
    ld.clear()
    mqttc.append_payload("plotter/live_devices", pickle.dumps(list(ld)))

    # shut off the smu
    measurement.sm.enable_output(False)

    # don't leave the light on!
    if hasattr(measurement, "le"):
        measurement.le.off()

    # stop daq
    mqttc.append_payload("daq/stop", pickle.dumps(""))


def _run(request, mqtthost):
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

    user_aborted = False

    args = request["args"]

    # calibrate spectrum if required
    if ("IV_stuff" in args) and (args["enable_solarsim"] is True):
        user_aborted = _calibrate_spectrum(request, mqtthost)

    if user_aborted is False:
        with MQTTQueuePublisher() as mqttc:
            mqttc.connect(mqtthost)
            mqttc.loop_start()
            try:
                with fabric() as measurement:
                    _log("Starting run...", 20, mqttc)
                    measurement.current_limit = request["config"]["smu"][
                        "current_limit"
                    ]

                    if "IV_stuff" in args:
                        q = _build_q(request, experiment="solarsim")
                        _ivt(q, request, measurement, mqttc)
                        measurement.disconnect_all_instruments()

                    # report complete
                    _log("Run complete!", 20, mqttc)

                print("Measurement complete.")
            except KeyboardInterrupt:
                pass
            except Exception as e:
                traceback.print_exc()
                _log(f"RUN ABORTED! " + str(e), 40, mqttc)

            mqttc.append_payload(
                "measurement/status", pickle.dumps("Ready"), retain=True
            )


def on_message(mqttc, obj, msg, msg_queue):
    """Add an MQTT message to the message queue."""
    msg_queue.put_nowait(msg)


def msg_handler(msg_queue, cli_args, process):
    """Handle MQTT messages in the msg queue.

    This function should run in a separate thread, polling the queue for messages.

    Actions that require instrument I/O run in a worker process. Only one action
    process can run at a time. If an action process is running the server will
    report that it's busy.
    """
    while True:
        msg = msg_queue.get()

        try:
            request = pickle.loads(msg.payload)
            action = msg.topic.split("/")[-1]

            # perform a requested action
            if (action == "run") and (
                (request["args"]["enable_eqe"] is True)
                or (request["args"]["enable_iv"] is True)
            ):
                process = start_process(
                    cli_args, process, _run, (request, cli_args.mqtthost)
                )
            elif action == "stop":
                process = stop_process(cli_args, process)
        except:
            pass

        msg_queue.task_done()


def main():
    """Get args and start MQTT."""
    # get command line arguments
    cli_args = get_args()

    # create dummy process
    process = multiprocessing.Process()

    # queue for storing incoming messages
    msg_queue = queue.Queue()

    # create mqtt client id
    client_id = f"measure-{uuid.uuid4().hex}"

    # setup mqtt subscriber client
    mqttc = mqtt.Client(client_id=client_id)
    mqttc.will_set("measurement/status", pickle.dumps("Offline"), 2, retain=True)
    mqttc.on_message = lambda mqttc, obj, msg: on_message(mqttc, obj, msg, msg_queue)
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

    msg_handler(msg_queue, cli_args, process)


# required when using multiprocessing in windows, advised on other platforms
if __name__ == "__main__":
    main()
