"""Client for running the CLI based on MQTT messages."""

import subprocess

import paho.mqtt.client as mqtt
import psutil

import central_control
from central_control.cli import cli


class CLIMQTT:

    def __init__(self):
        # psutils process object
        self.proc = None

    def __enter__(self):
        pass

    def __exit__(self):
        self._stop()
        self.loop_stop()
        self.disconnect()

    def on_message(self, mqttc, obj, msg):
        message = json.loads(msg.payload)

        if message["button"] == "run":
            self._run(message["cmd"])
        elif message["button"] == "pause":
            self._pause(message["cmd"])
        elif message["button"] == "stop":
            self._stop(message["cmd"])
        elif message["button"] == "cal_eqe":
            self._cal_eqe(message["cmd"])
        elif message["button"] == "cal_psu":
            self._cal_psu(message["cmd"])
        elif message["button"] == "home":
            self._home(message["cmd"])

    def _run(self, cmd):
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

    def _cal_eqe(self, cmd):
        if (self.proc is None) or (self.proc.status() == "dead"):
            args = 
            p = subprocess.Popen(["python", "cli.py", ])
            self.proc = psutil.Process(p.pid)
        else:
            pass

    def _cal_psu(self, cmd):
        if (self.proc is None) or (self.proc.status() == "dead"):
            args = 
            p = subprocess.Popen(["python", "cli.py", ])
            self.proc = psutil.Process(p.pid)
        else:
            pass

    def _home(self, cmd):
        if (self.proc is None) or (self.proc.status() == "dead"):
            args = 
            p = subprocess.Popen(["python", "cli.py", ])
            self.proc = psutil.Process(p.pid)
        else:
            pass

"""Create CLI with args received over MQTT."""
    mqtt_args = types.SimpleNamespace(**json.loads(msg.payload))
    cli = cli(mqtt_args, {})
    cli.run()
    mqttc.loop_stop()
    mqttc.disconnect()

if __name__ == "__main__":
    with CLIMQTT() as mqttc:
