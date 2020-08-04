import pickle

import paho.mqtt.client as mqtt


def on_message(mqttc, obj, msg):
    """Print log message."""
    print(pickle.loads(msg.payload))


if __name__ == "__main__":
    mqttc = mqtt.Client()
    mqttc.on_message = on_message
    mqttc.connect("127.0.0.1")
    mqttc.subscribe("measurement/log", qos=2)
    mqttc.loop_forever()
