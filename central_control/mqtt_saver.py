"""Save data obtained from MQTT broker."""

import csv
import json
import pathlib
import uuid

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


def save_data(payload, processed=False):
    """Save data to text file.

    Parameters
    ----------
    payload : str
        MQTT message payload.
    processed : bool
        Flag for highlighting when data has been processed.
    """
    kind = payload["kind"]
    data = payload["data"]

    exp = kind.replace("_measurement", "")
    if folder is not None:
        save_folder = folder
    else:
        save_folder = pathlib.Path()

    if processed is True:
        save_folder = save_folder.joinpath("processed")

    save_path = save_folder.joinpath(f"{data['id']}_{exp_timestamp}.{exp}")

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
            writer.writerows(data)
        else:
            writer.writerow(data)


def save_calibration(payload):
    """Save calibration data.

    Parameters
    ----------
    mqttc : mqtt.Client
        MQTT save client.
    """
    save_folder = pathlib.Path("calibration")

    cal_packet = payload["data"]

    if (kind := payload["kind"]) == "eqe_calibration":
        for key, value in cal_packet:
            diode = key
            timestamp = value["timestamp"]
            data = value["data"]
            save_path = save_folder.joinpath(f"{timestamp}_{diode}_eqe.cal")
            if save_path.exists() is False:
                with open(save_path, "w", newline="\n") as f:
                    f.writelines(eqe_header)
                with open(save_path, "a", newline="\n") as f:
                    writer = csv.writer(f, delimiter="\t")
                    writer.writerows(data)
    elif kind == "spectrum_calibration":
        timestamp = cal_packet["timestamp"]
        data = cal_packet["data"]
        save_path = save_folder.joinpath(f"{timestamp}_spectrum.cal")
        if save_path.exists() is False:
            with open(save_path, "w", newline="\n") as f:
                f.writelines(spectrum_cal_header)
            with open(save_path, "a", newline="\n") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerows(data)
    elif kind == "solarsim_diode_calibration":
        for key, value in cal_packet:
            diode = key
            timestamp = value["timestamp"]
            data = value["data"]
            save_path = save_folder.joinpath(f"{timestamp}_{diode}_solarsim.cal")
            if save_path.exists() is False:
                with open(save_path, "w", newline="\n") as f:
                    f.writelines(iv_header)
                with open(save_path, "a", newline="\n") as f:
                    writer = csv.writer(f, delimiter="\t")
                    writer.writerows(data)
    elif kind == "psu_calibration":
        for key, value in cal_packet:
            diode = key
            timestamp = value["timestamp"]
            data = value["data"]
            save_path = save_folder.joinpath(f"{timestamp}_{diode}_psu.cal")
            if save_path.exists() is False:
                with open(save_path, "w", newline="\n") as f:
                    f.writelines(psu_cal_header)
                with open(save_path, "a", newline="\n") as f:
                    writer = csv.writer(f, delimiter="\t")
                    writer.writerows(data)
    elif kind == "rtd_calibration":
        for key, value in cal_packet:
            rtd = key
            timestamp = value["timestamp"]
            data = value["data"]
            save_path = save_folder.joinpath(f"{timestamp}_{rtd}_rtd.cal")
            if save_path.exists() is False:
                with open(save_path, "w", newline="\n") as f:
                    f.writelines(iv_header)
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
    payload = json.loads(msg.payload)

    if (topic := msg.topic) == "data/raw":
        save_data(payload)
    elif topic == "data/processed":
        save_data(payload, processed=True)
    elif msg.topic.split("/")[0] == "calibration":
        save_calibration(payload)
    elif topic == "measurement/request":
        if payload["action"] == "run":
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
    mqttc.subscribe("data/raw", qos=2)
    mqttc.subscribe("data/processed", qos=2)
    mqttc.subscribe("calibration/#", qos=2)
    mqttc.subscribe("measurement/request", qos=2)

    mqttc.loop_forever()
