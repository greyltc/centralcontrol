"""Save data obtained from MQTT broker."""

import csv
import pathlib
import pickle
import uuid

from datetime import datetime

import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish
import yaml


# make header strings
eqe_header = (
    "timestamp (s)\twavelength (nm)\tX (V)\tY (V)\tAux In 1 (V)\tAux"
    + " In 2 (V)\tAux In 3 (V)\tAux In 4 (V)\tR (V)\tPhase (deg)\tFreq"
    + " (Hz)\tCh1 display\tCh2 display\tR/Aux In 1\n"
)
eqe_processed_header = eqe_header[:-1] + "\tEQE\n"
iv_header = "voltage (v)\tcurrent (A)\ttime (s)\tstatus\n"
iv_processed_header = (
    iv_header[:-1] + "\tcurrent_density (mA/cm^2)\tpower_density (mW/cm^2)\n"
)
spectrum_cal_header = "wls (nm)\traw (counts)\n"
psu_cal_header = "voltage (v)\tcurrent (A)\ttime (s)\tstatus\tpsu_current (A)\n"


def save_data(payload, kind, processed=False):
    """Save data to text file.

    Parameters
    ----------
    payload : str
        MQTT message payload.
    processed : bool
        Flag for highlighting when data has been processed.
    """
    if kind == "iv_measurement":
        if payload["sweep"] == "dark":
            exp_prefix = "d"
        elif payload["sweep"] == "light":
            exp_prefix = "l"
    else:
        exp_prefix = ""

    print(f"Saving {kind} data...")

    exp = f"{exp_prefix}{kind.replace('_measurement', '')}"

    if folder is not None:
        save_folder = folder
    else:
        save_folder = pathlib.Path()

    if processed is True:
        save_folder = save_folder.joinpath("processed")
        file_prefix = "processed_"
        if save_folder.exists() is False:
            save_folder.mkdir()
    else:
        file_prefix = ""

    save_path = save_folder.joinpath(
        f"{file_prefix}{payload['idn']}_{exp_timestamp}.{exp}"
    )

    print(save_path)

    # create file with header if pixel
    if save_path.exists() is False:
        with open(save_path, "w", newline="\n") as f:
            if exp == "eqe":
                if processed is True:
                    f.writelines(eqe_processed_header)
                else:
                    f.writelines(eqe_header)
            else:
                if processed is True:
                    f.writelines(iv_processed_header)
                else:
                    f.writelines(iv_header)

    # append data to file
    with open(save_path, "a", newline="\n") as f:
        writer = csv.writer(f, delimiter="\t")
        if (exp == "liv") or (exp == "div"):
            writer.writerows(payload["data"])
        else:
            writer.writerow(payload["data"])


def save_calibration(payload, kind, extra=None):
    """Save calibration data.

    Parameters
    ----------
    mqttc : mqtt.Client
        MQTT save client.
    kind : str
        Kind of calibration data.
    extra : str
        Extra information about the calibration type added to the filename.
    """
    print(f"Saving {kind} calibration...")
    save_folder = pathlib.Path("calibration")
    if save_folder.exists() is False:
        save_folder.mkdir()

    # format timestamp into something human readable including
    timestamp = payload["timestamp"]
    # local timezone
    timezone = datetime.now().astimezone().tzinfo
    fmt = "[%Y-%m-%d]_[%H-%M-%S_%z]"
    human_timestamp = datetime.fromtimestamp(timestamp, tz=timezone).strftime(f"{fmt}")

    data = payload["data"]

    if kind == "eqe":
        idn = payload["diode"]
        save_path = save_folder.joinpath(f"{human_timestamp}_{idn}_{kind}.cal")
        header = eqe_header
    elif kind == "spectrum":
        save_path = save_folder.joinpath(f"{human_timestamp}_{kind}.cal")
        header = spectrum_cal_header
    elif (kind == "solarsim_diode") or (kind == "rtd"):
        idn = payload["diode"]
        save_path = save_folder.joinpath(f"{human_timestamp}_{idn}_{kind}.cal")
        header = iv_header
    elif kind == "psu":
        idn = payload["diode"]
        save_path = save_folder.joinpath(f"{human_timestamp}_{idn}_{extra}_{kind}.cal")
        header = psu_cal_header

    if save_path.exists() is False:
        with open(save_path, "w", newline="\n") as f:
            f.writelines(header)
        with open(save_path, "a", newline="\n") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerows(data)


def save_run_settings(payload):
    """Save arguments parsed to server run command.

    Parameters
    ----------
    args : dict
        Arguments parsed to server run command.
    """
    global folder
    global exp_timestamp

    folder = pathlib.Path(payload["args"]["run_name"])
    if folder.exists() is False:
        folder.mkdir()

    exp_timestamp = pathlib.PurePath(folder).parts[-1][-10:]

    # save args
    with open(folder.joinpath(f"run_args_{exp_timestamp}.yaml"), "w") as f:
        yaml.dump(payload["args"], f)

    # save config
    with open(folder.joinpath(f"measurement_config_{exp_timestamp}.yaml"), "w") as f:
        yaml.dump(payload["config"], f)


def on_message(mqttc, obj, msg):
    """Act on an MQTT msg."""
    payload = pickle.loads(msg.payload)
    print(msg.topic, payload)
    topic_list = msg.topic.split("/")

    if (topic := topic_list[0]) == "data":
        if (subtopic0 := topic_list[1]) == "raw":
            save_data(payload, topic_list[2])
        elif subtopic0 == "processed":
            save_data(payload, topic_list[2], True)
    elif topic == "calibration":
        if topic_list[1] == "psu":
            subtopic1 = topic_list[2]
        else:
            subtopic1 = None
        save_calibration(payload, topic_list[1], subtopic1)
    elif msg.topic == "measurement/run":
        save_run_settings(payload)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-mqtthost",
        type=str,
        default="127.0.0.1",
        help="IP address or hostname for MQTT broker.",
    )

    args = parser.parse_args()

    # init global variables
    folder = None
    exp_timestamp = ""

    # create mqtt client id
    client_id = f"saver-{uuid.uuid4().hex}"

    mqttc = mqtt.Client(client_id)
    mqttc.will_set("saver/status", pickle.dumps(f"{client_id} offline"), 2, retain=True)
    mqttc.on_message = on_message
    mqttc.connect(args.mqtthost)
    mqttc.subscribe("data/#", qos=2)
    mqttc.subscribe("calibration/#", qos=2)
    mqttc.subscribe("measurement/#", qos=2)
    publish.single(
        "saver/status",
        pickle.dumps(f"{client_id} ready"),
        qos=2,
        hostname=args.mqtthost,
    )
    print(f"{client_id} connected!")
    mqttc.loop_forever()
