import paho.mqtt.client as mqtt
import pickle

raw_ivt_data = [1, 1, 1, 1]
raw_ivt_payload = {
    "data": raw_ivt_data,
    "idn": "test",
    "clear": False,
    "end": False,
    "sweep": "light",
}

processed_ivt_data = [1, 1, 1, 1, 2, 2]
processed_ivt_payload = {
    "data": processed_ivt_data,
    "idn": "test",
    "clear": False,
    "end": False,
    "sweep": "light",
}

if __name__ == "__main__":
    mqttc = mqtt.Client()
    mqttc.connect("127.0.0.1")
    mqttc.loop_start()
    mqttc.publish(
        "data/raw/iv_measurement", pickle.dumps(raw_ivt_payload), 2
    ).wait_for_publish()

