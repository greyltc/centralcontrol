#!/usr/bin/env python3
"""MQTT Client to facilitate tx/rxing messages to/from the broker"""

import sys
import threading
import json
import uuid
import logging
import typing
import paho.mqtt.client as mqtt
from queue import SimpleQueue as Queue
from multiprocessing.queues import SimpleQueue as mQueue
from concurrent.futures import Executor

from centralcontrol.logstuff import get_logger
from centralcontrol.logstuff import NewHandler


class MQTTClient(object):
    """interfaces with he MQTT message broker server"""

    # for outgoing messages
    outq: Queue | mQueue

    # for incoming messages
    inq: Queue

    # return code
    retcode = 0

    host: str
    port: int

    client_id: str

    mqttc: mqtt.Client

    lg: logging.Logger

    workers: list[threading.Thread]  # list of things doing work for us

    def __init__(self, host="127.0.0.1", port=1883, parent_outq: None | Queue | mQueue = None, parent_inq: None | Queue = None):
        self.workers = []
        if parent_outq:
            self.outq = parent_outq
        else:
            self.outq = Queue()

        if parent_inq:
            self.inq = parent_inq
        else:
            self.inq = Queue()

        self.lg = get_logger(".".join([__name__, type(self).__name__]))  # setup logging

        # add the ability for some log messages to be sent to the broker
        # add the handler for that at the package level so everyone can use it
        pkglg = logging.getLogger(__package__)
        lh = NewHandler(self.send_log_msg)
        lh.setLevel(29)  # special level for filtering messages to the broker
        pkglg.addHandler(lh)

        self.host = host
        self.port = port

        # create mqtt client id
        self.client_id = f"measure-{uuid.uuid4().hex}"

        self.lg.debug("Initialized.")

    def __enter__(self):
        """Enter the runtime context related to this object."""
        # setup mqtt subscriber client
        self.mqttc = mqtt.Client(client_id=self.client_id)

        # sticky an Offline message in the status channel if we disconnect unexpectedly
        self.mqttc.will_set("measurement/status", json.dumps("Offline"), 2, retain=True)

        # register some callbacks
        self.mqttc.on_message = self.on_message
        self.mqttc.on_connect = self.on_connect
        self.mqttc.on_disconnect = self.on_disconnect

        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.disconnect()
        return False

    # send up a log message to the status channel
    def send_log_msg(self, record: logging.LogRecord):
        payload = {"level": record.levelno, "msg": record.msg}  # TODO: consider sending up the unmodified record
        # payload = record
        self.outq.put({"topic": "measurement/log", "payload": json.dumps(payload), "qos": 2})

    def on_message(self, client: mqtt.Client, userdata: typing.Any, msg: mqtt.MQTTMessage):
        """The callback for when a message appears in a channel we're subscribed to"""
        try:
            self.inq.put(msg)  # put the message in the incomming queue
        except Exception as e:
            self.lg.error(f"Failure handing incomming message: {e}")

    # when client connects to broker
    def on_connect(self, client: mqtt.Client, userdata, flags, rc):
        self.lg.debug(f"mqtt_server connected to broker with result code {rc}")
        client.subscribe("measurement/#", qos=2)  # for measurement messages
        client.subscribe("cmd/#", qos=2)  # for utility messages
        client.publish("measurement/status", json.dumps("Ready"), qos=2, retain=True)

    # when client disconnects from broker
    def on_disconnect(self, client: mqtt.Client, userdata, rc):
        self.lg.debug(f"Disconnected from broker with result code {rc}")

    # relays outgoing messages
    def out_relay(self):
        """
        forever gets messages that were put into the output queue and sends them to the broker
        if this is run as a daemon thread it will be cleaned up when the main process comes to an end
        """
        while True:
            to_send = self.outq.get()
            try:
                if to_send == "die":
                    break
                else:
                    self.mqttc.publish(**to_send).wait_for_publish()
            except Exception as e:
                self.lg.error(f"Error publishing message to broker: {e}")
        self.lg.debug("Out queue relay stopped")

    def start_loop(self) -> int:
        """spawn a thread and maintain the mqtt loop in there"""
        self.mqttc.connect_async(self.host, self.port)
        try:
            self.mqttc.loop_start()  #  the thread created here is self.mqttc._thread_main
            # self.workers.append(self.mqttc._thread)  # type: ignore
        except Exception as e:
            self.lg.error(f"Message broker loop start failure: {e}")
            retcode = -1
        else:
            retcode = 0
        return retcode

    def run_loop(self):
        """run the blocking mqtt connection maintenance loop"""
        self.mqttc.connect_async(self.host, self.port)
        try:
            self.mqttc.loop_forever()
        except Exception as e:
            self.lg.error(f"Message broker loop failure: {e}")
            self.retcode = -5

    def disconnect(self):
        """disconnects from the message broker"""

        # sticky an offline message in the status channel. blocking send because we're about to shut down
        self.mqttc.publish("measurement/status", json.dumps("Offline"), qos=2, retain=True).wait_for_publish()

        # ask the out_relay to stop
        self.outq.put("die")

        try:
            self.mqttc.disconnect()
        except:
            pass
        try:
            self.mqttc.loop_stop()
        except:
            pass
        for worker in self.workers:
            try:
                worker.join(1.0)
            except:
                pass

    def run(self) -> int:
        """runs one handler in a thread and the main mqtt loop in the forground.
        blocks forever (or until .disconnect() is called)"""

        # start the outq handler thread (sends messages to the broker)
        self.workers.append(threading.Thread(target=self.out_relay, daemon=False))
        self.workers[-1].start()

        # begin mqtt loop maintanince (blocks here)
        self.run_loop()

        return self.retcode

    def start(self):
        """starts the mqtt connection nonblockingly in two threads"""
        # start the outq handler thread (sends messages to the broker)
        self.workers.append(threading.Thread(target=self.out_relay, daemon=False))
        self.workers[-1].start()

        self.start_loop()  # start the client-broker mainintance loop in a background thread

    def execute(self, executer: Executor):
        """run this via an external Executer"""
        pass


if __name__ == "__main__":
    sys.exit(MQTTClient().run())
