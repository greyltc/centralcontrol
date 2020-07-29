"""Save data obtained from MQTT broker."""

import csv
import pathlib
import pickle
import uuid

from datetime import datetime

import paho.mqtt.client as mqtt
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
    if payload["sweep"] == "dark":
        prefix = "d"
    elif payload["sweep"] == "light":
        prefix = "l"
    else:
        prefix = ""

    exp = f"{prefix}{kind.replace('_measurement', '')}"

    if folder is not None:
        save_folder = folder
    else:
        save_folder = pathlib.Path()

    if processed is True:
        save_folder = save_folder.joinpath("processed")
        if save_folder.exists() is False:
            save_folder.mkdir()

    save_path = save_folder.joinpath(f"{payload['idn']}_{exp_timestamp}.{exp}")

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
        if exp == "iv":
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
    print("saving calibration...")
    save_folder = pathlib.Path("calibration")
    if save_folder.exists() is False:
        save_folder.mkdir()

    timestamp = payload["timestamp"]
    # local timezone
    timezone = datetime.now().astimezone().tzinfo
    fmt = "[%Y-%m-%d]_[%H-%M-%S_%z]"
    human_timestamp = datetime.fromtimestamp(timestamp, tz=timezone).strftime(f"{fmt}")

    if kind == "eqe":
        print("saving eqe...")
        idn = payload["diode"]
        data = payload["data"]
        save_path = save_folder.joinpath(f"{human_timestamp}_{idn}_eqe.cal")
        print(save_path)
        if save_path.exists() is False:
            print("saving...")
            with open(save_path, "w", newline="\n") as f:
                f.writelines(eqe_header)
            with open(save_path, "a", newline="\n") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerows(data)
    elif kind == "spectrum":
        timestamp = payload["timestamp"]
        data = payload["data"]
        save_path = save_folder.joinpath(f"{human_timestamp}_spectrum.cal")
        if save_path.exists() is False:
            with open(save_path, "w", newline="\n") as f:
                f.writelines(spectrum_cal_header)
            with open(save_path, "a", newline="\n") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerows(data)
    elif kind == "solarsim_diode":
        idn = payload["diode"]
        timestamp = payload["timestamp"]
        data = payload["data"]
        save_path = save_folder.joinpath(f"{human_timestamp}_{idn}_solarsim.cal")
        print(save_path)
        if save_path.exists() is False:
            with open(save_path, "w", newline="\n") as f:
                f.writelines(iv_header)
            with open(save_path, "a", newline="\n") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerows(data)
    elif kind == "psu":
        idn = payload["diode"]
        timestamp = payload["timestamp"]
        data = payload["data"]
        save_path = save_folder.joinpath(f"{human_timestamp}_{idn}_{extra}_psu.cal")
        if save_path.exists() is False:
            with open(save_path, "w", newline="\n") as f:
                f.writelines(psu_cal_header)
            with open(save_path, "a", newline="\n") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerows(data)
    elif kind == "rtd":
        idn = payload["diode"]
        timestamp = payload["timestamp"]
        data = payload["data"]
        save_path = save_folder.joinpath(f"{human_timestamp}_{idn}_rtd.cal")
        if save_path.exists() is False:
            with open(save_path, "w", newline="\n") as f:
                f.writelines(iv_header)
            with open(save_path, "a", newline="\n") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerows(data)

    print(save_path)


def save_run_settings(payload):
    """Save arguments parsed to server run command.

    Parameters
    ----------
    args : dict
        Arguments parsed to server run command.
    """
    global folder
    global exp_timestamp

    folder = pathlib.Path(payload["args"]["destination"])
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
    print(msg.topic)
    print(payload)
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
        print(topic_list[1])
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
    mqttc.on_message = on_message
    mqttc.connect(args.mqtthost)
    mqttc.subscribe("data/#", qos=2)
    mqttc.subscribe("calibration/#", qos=2)
    mqttc.subscribe("measurement/#", qos=2)

    print("connected!")

    mqttc.loop_forever()
