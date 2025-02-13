"""central control package"""

from centralcontrol.__about__ import __version__, __version_tuple__
from centralcontrol.fabric import Fabric
import multiprocessing
import argparse
import os
import sys


class CentralControl(object):
    mem_db_url: str = "redis://"
    mem_db_url_env_name: str = "MEM_DB_URL"
    run_params: dict
    exitcode = 0

    def __init__(self):
        self.run_params = {}

    def mqtt_cli(self):
        """gather mqtt specific params from the user"""
        default_mqtt_server_host = "127.0.0.1:1883"
        default_mqtt_server_port = 1883

        parser = argparse.ArgumentParser(
            prog="centralcontrol",
            description="backend program to orchestrate the execution of the measurement routine",
        )
        parser.epilog = f'example usage: centralcontrol --mqtthost="{default_mqtt_server_host}"'
        parser.add_argument("--mqtthost", default=default_mqtt_server_host, help="host[:port] of the MQTT message broker")
        parser.add_argument("--mem-db-url", help="Memory database connection string")

        self.run_params = vars(parser.parse_args())

        if self.run_params["mem_db_url"]:
            self.mem_db_url = self.run_params["mem_db_url"]
        elif self.mem_db_url_env_name in os.environ:
            self.mem_db_url = os.environ[self.mem_db_url_env_name]

        # hande port
        if ":" in self.run_params["mqtthost"]:
            hostport = self.run_params["mqtthost"].split(":", 1)
            self.run_params["mqtthost"] = hostport[0]
            self.run_params["mqttport"] = int(hostport[1])
        else:
            self.run_params["mqttport"] = default_mqtt_server_port

    def mqtt_run(self) -> int:
        """run the server in mqtt mode"""
        f = Fabric(mem_db_url=self.mem_db_url)
        # set the connection parameters
        f.mqtt_host = self.run_params["mqtthost"]
        f.mqtt_port = self.run_params["mqttport"]
        # mc = MQTTClient(mqtthost=self.run_params["mqtthost"], port=self.run_params["mqttport"])
        self.exitcode = f.run()
        return self.exitcode

    def cli(self):
        """gather params from the user via the command line"""
        self.mqtt_cli()

    def run(self) -> int:
        """run the program"""
        self.exitcode = self.mqtt_run()
        return self.exitcode


def main():
    cc = CentralControl()
    cc.cli()
    cc.run()
    sys.exit(cc.exitcode)


if __name__ == "__main__":
    main()
