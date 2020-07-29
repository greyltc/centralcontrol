import paho.mqtt.client as mqtt
import pickle
import time

timestamp = time.time()


def test_saver():
    """Test the saver client."""
    raw_ivt_data = [1, 1, 1, 1]
    raw_ivt_payload = {
        "data": raw_ivt_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "light",
    }

    raw_iv_data = [[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]]
    raw_iv_payload = {
        "data": raw_iv_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "light",
    }

    raw_eqe_data = [1] * 14
    raw_eqe_payload = {
        "data": raw_eqe_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "",
    }

    processed_ivt_data = [1, 1, 1, 1, 2, 2]
    processed_ivt_payload = {
        "data": processed_ivt_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "light",
    }

    processed_iv_data = [[1, 1, 1, 1, 2, 2], [1, 1, 1, 1, 2, 2], [1, 1, 1, 1, 2, 2]]
    processed_iv_payload = {
        "data": processed_iv_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "light",
    }

    processed_eqe_data = [1] * 15
    processed_eqe_payload = {
        "data": processed_eqe_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "",
    }

    cal_eqe_data = [raw_eqe_data, raw_eqe_data, raw_eqe_data]
    cal_eqe_payload = {"data": cal_eqe_data, "idn": "test", "timestamp": timestamp}

    cal_spectrum_data = [[1, 1], [1, 1], [1, 1]]
    cal_spectrum_payload = {
        "data": cal_spectrum_data,
        "idn": "test",
        "timestamp": timestamp,
    }

    cal_solarsim_diode_data = [raw_ivt_data, raw_ivt_data, raw_ivt_data]
    cal_solarsim_diode_payload = {
        "data": cal_solarsim_diode_data,
        "idn": "test",
        "timestamp": timestamp,
    }

    cal_rtd_data = cal_solarsim_diode_data
    cal_rtd_payload = {
        "data": cal_rtd_data,
        "idn": "test",
        "timestamp": timestamp,
    }

    cal_psu_data = [[1, 1, 1, 1, 1], [1, 1, 1, 1, 1], [1, 1, 1, 1, 1]]
    cal_psu_payload = {
        "data": cal_psu_data,
        "idn": "test",
        "timestamp": timestamp,
    }

    mqttc.publish(
        "data/raw/vt_measurement", pickle.dumps(raw_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/it_measurement", pickle.dumps(raw_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/mppt_measurement", pickle.dumps(raw_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/iv_measurement", pickle.dumps(raw_iv_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/eqe_measurement", pickle.dumps(raw_eqe_payload), 2
    ).wait_for_publish()

    mqttc.publish(
        "data/processed/vt_measurement", pickle.dumps(processed_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/processed/it_measurement", pickle.dumps(processed_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/processed/mppt_measurement", pickle.dumps(processed_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/processed/iv_measurement", pickle.dumps(processed_iv_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/processed/eqe_measurement", pickle.dumps(processed_eqe_payload), 2
    ).wait_for_publish()

    mqttc.publish(
        "calibration/eqe", pickle.dumps(cal_eqe_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "calibration/spectrum", pickle.dumps(cal_spectrum_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "calibration/solarsim_diode", pickle.dumps(cal_solarsim_diode_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "calibration/rtd", pickle.dumps(cal_rtd_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "calibration/psu/ch1", pickle.dumps(cal_psu_payload), 2
    ).wait_for_publish()


if __name__ == "__main__":
    mqttc = mqtt.Client()
    mqttc.connect("127.0.0.1")
    mqttc.loop_start()

    test_saver()

    mqttc.loop_stop()
    mqttc.disconnect()
