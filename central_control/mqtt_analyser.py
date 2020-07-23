"""Analyse raw data and publish the analysis."""

import argparse
import json

import numpy as np
import scipy as sp

import scipy.interpolate
from mqtt_tools.queue_publisher import MQTTQueuePublisher


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


def process_ivt(payload, mqttc):
    """Calculate derived I-V-t parameters.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    """
    data = payload["data"]
    area = payload["area"]

    # calculate current density in mA/cm2
    j = data[1] * 1000 / area
    data.append(j)

    # calculate power density
    p = j * data[0]
    data.append(p)

    # add processed data back into payload to be sent on
    payload["data"] = data
    payload = json.dumps(payload)
    mqttc.append_payload("data/processed", payload)


def process_iv(payload, mqttc):
    """Calculate derived I-V parameters.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    """
    data = np.array(payload["data"])
    area = payload["area"]

    # calculate current density in mA/cm2
    j = data[:, 1] * 1000 / area
    data[:, 4] = j

    # calculate power density in mW/cm2
    p = j * data[0]
    data[:, 5] = p

    # add processed data back into payload to be sent on
    payload["data"] = data
    mqttc.append_payload("data/processed", json.dumps(payload))


def process_eqe(payload, mqttc):
    """Calculate EQE.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    """
    # read measurement
    meas = payload["data"]
    meas_wl = meas[1]
    meas_sig = meas[-1]

    # get interpolation object
    cal = np.array(eqe_calibration["data"])
    cal_wls = cal[:, 1]
    cal_sig = cal[:, -1]
    f_cal = sp.interpolate.interp1d(
        cal_wls, cal_sig, kind="cubic", bounds_error=False, fill_value=0
    )

    # look up ref eqe
    diode = config["experiments"]["eqe"]["calibration_diode"]
    ref_wls = config["calibration_diodes"][diode]["eqe"]["eqe_calibration_settings"][
        "eqe"
    ]["wls"]
    ref_eqe = config["calibration_diodes"][diode]["eqe"]["eqe_calibration_settings"][
        "eqe"
    ]["eqe"]
    f_ref = sp.interpolate.interp1d(
        ref_wls, ref_eqe, kind="cubic", bounds_error=False, fill_value=0
    )

    # calculate eqe and append to data
    meas_eqe = f_ref(meas_wl) * meas_sig / f_cal(meas_wl)
    meas.append(meas_eqe)

    # publish
    payload["data"] = meas
    mqttc.append_payload("data/processed", json.dumps(payload))


def process_spectrum(mqttc):
    """Convert spectrum measurement into spectral irradiance.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    mqttc : MQTTQueuePublisher
        MQTT queue publisher client.
    """
    # look up calibration and measurement data
    cal = np.array(config["solarsim"]["spectral_calibration"]["cal"])
    meas = np.array(spectrum_calibration["data"]["meas"])

    # calculate spectral irradiance in W/m^2/nm and append to cal dict
    irr = meas * cal
    irr = irr.tolist()
    spectrum_calibration["data"]["irr"] = irr

    # publish processed spectrum
    mqttc.append_payload("data/processed", json.dumps(spectrum_calibration))


def read_eqe_cal(payload):
    """Read calibration from payload.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    """
    global eqe_calibration

    eqe_calibration = payload["calibration"]


def read_psu_cal(payload):
    """Read calibration from payload.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    """
    global psu_calibration

    psu_calibration = payload["calibration"]


def read_solarsim_diode_cal(payload):
    """Read calibration from payload.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    """
    global solarsim_diode_calibration

    solarsim_diode_calibration = payload["calibration"]


def read_spactrum_cal(payload):
    """Read calibration from payload.

    Parameters
    ----------
    payload : dict
        Payload dictionary.
    """
    global spectrum_calibration

    spectrum_calibration = payload["calibration"]


def read_config(payload):
    """Get config data from payload.

    Parameters
    ----------
    payload : dict
        Request dictionary for measurement server.
    """
    global config

    config = payload["config"]


def send_calibration_status(mqttc):
    """Send calibration status."""
    # gather status of each calibration
    calibration_status = {
        "eqe": eqe_calibration != {},
        "psu": psu_calibration != {},
        "spectrum": spectrum_calibration != {},
        "solarsim_diode": solarsim_diode_calibration != {},
    }

    # publish status
    payload = json.dumps(calibration_status)
    mqttc.append_payload("control/calibration_check_response", payload)


def on_message(mqttc, obj, msg):
    """Act on an MQTT message."""
    payload = json.loads(msg.payload)

    if (topic := msg.topic) == "data/raw":
        if (measurement := payload["measurement"]) in [
            "vt_measurement",
            "it_measurement",
            "mppt_measurement",
        ]:
            process_ivt(payload, mqttc)
        elif measurement == "iv_measurement":
            process_iv(payload, mqttc)
        elif measurement == "eqe_measurement":
            process_eqe(payload, mqttc)
    elif topic == "data/calibration":
        if (measurement := payload["measurement"]) == "eqe_calibration":
            read_eqe_cal(payload)
        elif measurement == "psu_calibration":
            read_psu_cal(payload)
        elif measurement == "solarsim_diode":
            read_solarsim_diode_cal(payload)
        elif measurement == "spectrum_calibration":
            read_spactrum_cal(payload)
    elif topic == "measurement/request":
        read_config(payload)
        if payload["action"] == "run":
            process_spectrum(mqttc)
    elif topic == "control/calibration_check_request":
        send_calibration_status(mqttc)


if __name__ == "__main__":
    args = get_args()

    # init empty dicts for caching latest data
    config = {}
    eqe_calibration = {}
    psu_calibration = {}
    spectrum_calibration = {}
    solarsim_diode_calibration = {}

    with MQTTQueuePublisher() as mqtt_analyser:
        mqtt_analyser.on_message = on_message

        # connect MQTT client to broker
        mqtt_analyser.connect(args.MQTTHOST)

        # subscribe to data and request topics
        mqtt_analyser.subscribe("data/raw")
        mqtt_analyser.subscribe("data/calibration")
        mqtt_analyser.subscribe("measurement/request")
        mqtt_analyser.subscribe("control/calibration_check_request")

        # start publisher queue for processing responses
        mqtt_analyser.loop_start()
        mqtt_analyser.start_q()

        mqtt_analyser.loop_forever()
