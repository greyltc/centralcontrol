"""central control package"""
from centralcontrol.mqtt import MQTTClient
from centralcontrol.fabric import Fabric
import argparse


class CentralControl(object):
    run_params = {}
    exitcode = 0

    def __init__(self):
        pass

    def mqtt_cli(self):
        default_mqtt_server_host = "127.0.0.1:1883"
        default_mqtt_server_port = 1883

        parser = argparse.ArgumentParser(
            prog="centralcontrol",
            description="backend program to orchestrate the execution of the measurement routine",
        )
        parser.epilog = f'example usage: centralcontrol --mqtthost="{default_mqtt_server_host}"'
        parser.add_argument("--mqtthost", default=default_mqtt_server_host, help="host[:port] of the MQTT message broker")

        self.run_params = vars(parser.parse_args())

        # hande port
        if ":" in self.run_params["mqtthost"]:
            hostport = self.run_params["mqtthost"].split(":", 1)
            self.run_params["mqtthost"] = hostport[0]
            self.run_params["mqttport"] = int(hostport[1])
        else:
            self.run_params["mqttport"] = default_mqtt_server_port

    def mqtt_run(self) -> int:
        mc = MQTTClient(mqtthost=self.run_params["mqtthost"], port=self.run_params["mqttport"])
        self.exitcode = mc.run()
        return self.exitcode

    def cli(self):
        self.mqtt_cli()

    def run(self) -> int:
        self.exitcode = self.mqtt_run()
        return self.exitcode


def main() -> int:
    cc = CentralControl()
    cc.cli()
    cc.run()
    return cc.exitcode


if __name__ == "__main__":
    main()
