"""Analyse raw data and publish the analysis."""

import argparse
import pickle
import threading
import uuid

import numpy as np
import scipy as sp

import scipy.interpolate
import paho.mqtt.client as mqtt


def get_args():
    """Get command line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-mqtthost",
        type=str,
        default="127.0.0.1",
        help="IP address or hostname for MQTT broker.",
    )
    return parser.parse_args()


def process_ivt(payload, kind):
    """Calculate derived I-V-t parameters.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    kind : str
        Kind of measurement data.
    """
    print("processing ivt...")

    data = payload["data"]
    area = payload["pixel"]["area"]

    # calculate current density in mA/cm2
    j = data[1] * 1000 / area
    p = data[0] * j
    data.append(j)
    data.append(p)

    # add processed data back into payload to be sent on
    payload["data"] = data
    payload = pickle.dumps(payload)
    _publish(f"data/processed/{kind}", payload)

    print("ivt processed!")


def process_iv(payload):
    """Calculate derived I-V parameters.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    """
    print("processing iv...")

    data = np.array(payload["data"])
    area = payload["pixel"]["area"]

    # calculate current density in mA/cm2
    j = data[:, 1] * 1000 / area
    p = data[:, 0] * j
    data = np.append(data, j.reshape(len(p), 1), axis=1)
    data = np.append(data, p.reshape(len(p), 1), axis=1)

    # add processed data back into payload to be sent on
    payload["data"] = data.tolist()
    _publish("data/processed/iv_measurement", pickle.dumps(payload))

    print("iv processed...")


def process_eqe(payload):
    """Calculate EQE.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    """
    print("processing eqe...")

    # read measurement
    meas = payload["data"]
    meas_wl = meas[1]
    meas_sig = meas[-1]

    # get interpolation object
    cal = np.array(eqe_calibration)
    cal_wls = cal[:, 1]
    cal_sig = cal[:, -1]
    f_cal = sp.interpolate.interp1d(
        cal_wls, cal_sig, kind="linear", bounds_error=False, fill_value=0
    )

    # look up ref eqe
    ref_wls = config["reference"]["calibration"]["eqe"]["wls"]
    ref_eqe = config["reference"]["calibration"]["eqe"]["eqe"]
    f_ref = sp.interpolate.interp1d(
        ref_wls, ref_eqe, kind="linear", bounds_error=False, fill_value=0
    )

    # calculate eqe and append to data
    meas_eqe = f_ref(meas_wl) * meas_sig / f_cal(meas_wl)
    meas.append(meas_eqe)

    # publish
    payload["data"] = meas
    _publish("data/processed/eqe_measurement", pickle.dumps(payload))

    print("eqe processed!")


def _publish(topic, payload):
    print("attempt publish...")

    t = threading.Thread(target=_publish_worker, args=(topic, payload,))
    t.start()


def _publish_worker(topic, payload):
    """Publish something over MQTT with a fresh client.
    
    Parameters
    ----------
    topic : str
        Topic to publish to.
    payload : 
    """
    print(f"publishing...")

    mqttc = mqtt.Client()
    mqttc.connect(args.mqtthost)
    mqttc.loop_start()
    mqttc.publish(topic, payload, 2).wait_for_publish()
    mqttc.loop_stop()
    mqttc.disconnect()


def read_eqe_cal(payload):
    """Read calibration from payload.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    """
    global eqe_calibration

    print("reading eqe cal...")

    eqe_calibration = payload["data"]


def read_config(payload):
    """Get config data from payload.

    Parameters
    ----------
    payload : dict
        Request dictionary for measurement server.
    """
    global config

    print("reading config...")

    config = payload["config"]


def on_message(mqttc, obj, msg):
    """Act on an MQTT message."""
    payload = pickle.loads(msg.payload)

    print(msg.topic, payload)

    topic_list = msg.topic.split("/")

    if (topic_list[0] == "data") and (topic_list[1] == "raw"):
        if (measurement := topic_list[2]) in [
            "vt_measurement",
            "it_measurement",
            "mppt_measurement",
        ]:
            process_ivt(payload, measurement)
        elif measurement == "iv_measurement":
            process_iv(payload)
        elif measurement == "eqe_measurement":
            process_eqe(payload)
    elif topic_list[0] == "calibration":
        if (measurement := topic_list[1]) == "eqe":
            read_eqe_cal(payload)
    elif msg.topic == "measurement/run":
        read_config(payload)


if __name__ == "__main__":
    args = get_args()

    # init empty dicts for caching latest data
    config = {}
    eqe_calibration = {}

    # create mqtt client id
    client_id = f"analyser-{uuid.uuid4().hex}"

    # mqtt_pub = mqtt.Client()
    # mqtt_pub.connect(args.mqtthost)

    mqtt_analyser = mqtt.Client(client_id)
    mqtt_analyser.on_message = on_message

    # connect MQTT client to broker
    mqtt_analyser.connect(args.mqtthost)

    # subscribe to data and request topics
    mqtt_analyser.subscribe("data/raw/#", qos=2)
    mqtt_analyser.subscribe("calibration/eqe", qos=2)
    mqtt_analyser.subscribe("measurement/run", qos=2)

    print(f"{client_id} connected!")

    mqtt_analyser.loop_forever()
