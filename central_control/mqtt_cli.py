"""Client for running the CLI based on MQTT messages."""

import subprocess

import paho.mqtt.client as mqtt
import psutil

import central_control
from central_control.cli import cli


class CLIMQTT(mqtt.Client):
    """MQTT client that controls how the CLI is run from the GUI."""

    def __init__(self, MQTTHOST="172.0.0.1", topic="gui/#"):
        """Construct object.

        Connect the MQTT client to the broker, subscribe to the GUI topic, and create
        process attribute (for storing cli process).

        Parameters
        ----------
        MQTTHOST : str
            IP address or host name of the MQTT broker.
        topic : str
            Topic to subscribe to.
        """
        # connect MQTT client to broker
        self.connect(MQTTHOST)
        # subscribe to everything in the GUI topic
        self.subscribe(topic)

        # psutils process object
        self.proc = None

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self):
        """Exit the runtime context related to this object.

        Make sure everything gets cleaned up properly.
        """
        self._stop()
        self.loop_stop()
        self.disconnect()

    def on_message(self, mqttc, obj, msg):
        """Act on an MQTT message."""
        m = json.loads(msg.payload)

        # perform action depending on which button generated the message
        if (button := msg.topic.split("/")[-1]) == "run":
            self._run(m)
        elif button == "pause":
            self._pause(m)
        elif button == "stop":
            self._stop(m)
        elif button == "cal_eqe":
            self._cal_eqe(m)
        elif button == "cal_psu":
            self._cal_psu(m)
        elif button == "home":
            self._home(m)

    def _run(self, msg):
        if (self.proc is None) or (status := self.proc.status()) == "dead":
            args = 
            p = subprocess.Popen(["python", "cli.py", ])
            self.proc = psutil.Process(p.pid)
        elif status == "suspended":
            self.proc.resume()
        else:
            pass

    def _pause(self):
        if (self.proc is not None) & (self.proc.status() == "running"):
            self.proc.suspend()
        else:
            pass

    def _stop(self):
        if (self.proc is not None) & (self.proc.status() != "dead"):
            self.proc.kill()
        else:
            pass

    def _cal_eqe(self, msg):
        if (self.proc is None) or (self.proc.status() == "dead"):
            args = 
            p = subprocess.Popen(["python", "cli.py", ])
            self.proc = psutil.Process(p.pid)
        else:
            pass

    def _cal_psu(self, msg):
        if (self.proc is None) or (self.proc.status() == "dead"):
            args = 
            p = subprocess.Popen(["python", "cli.py", ])
            self.proc = psutil.Process(p.pid)
        else:
            pass

    def _home(self, msg):
        if (self.proc is None) or (self.proc.status() == "dead"):
            args = 
            p = subprocess.Popen(["python", "cli.py", ])
            self.proc = psutil.Process(p.pid)
        else:
            pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mqtthost",
        default="172.0.0.1",
        help="IP address or hostname of MQTT broker.",
    )
    parser.add_argument(
        "--topic", default="gui/#", help="Topic for MQTT client to subscribe to.",
    )
    args = parser.parse_args()

    with CLIMQTT(args.mqtthost, args.topic) as mqttc:
        mqttc.loop_forever()
