#!/usr/bin/env python3
"""MQTT Client to facilitate tx/rxing messages to/from the broker"""

import sys
import multiprocessing
import threading
import json
import uuid
import logging
import typing
import paho.mqtt.client as mqtt
from threading import Event as tEvent
from multiprocessing.synchronize import Event as mEvent
from queue import SimpleQueue as Queue
from multiprocessing import SimpleQueue as mQueue
from concurrent.futures import Executor

# for logging directly to systemd journal if we can
try:
    import systemd.journal
except ImportError:
    pass


class MQTTClient(object):
    """interfaces with he MQTT message broker server"""

    # for outgoing messages
    outq: Queue | mQueue

    # for incoming messages
    inq: Queue | mQueue

    # long tasks get their own process
    # process = multiprocessing.Process()

    # return code
    retcode = 0

    host: str
    port: int

    client_id: str

    mqttc: mqtt.Client

    lg: logging.Logger

    # listen to this for kill signals
    killer: tEvent | mEvent

    workers: list[threading.Thread] = []  # list of things doing work for us

    class Dummy(object):
        pass

    def __init__(self, host="127.0.0.1", port=1883, use_threads=True):
        # setup logging
        logname = __name__
        if __package__ in __name__:
            # log at the package level if the imports are all correct
            logname = __package__
        self.lg = logging.getLogger(logname)
        self.lg.setLevel(logging.DEBUG)

        if not self.lg.hasHandlers():
            # set up a logging handler for passing messages to the UI log window
            uih = logging.Handler()
            uih.name = "remote"
            uih.setLevel(logging.INFO)
            uih.emit = self.send_log_msg
            self.lg.addHandler(uih)

            # set up logging to systemd's journal if it's there
            if "systemd" in sys.modules:
                sysdl = systemd.journal.JournalHandler(SYSLOG_IDENTIFIER=self.lg.name)
                sysLogFormat = logging.Formatter(("%(levelname)s|%(message)s"))
                sysdl.setFormatter(sysLogFormat)
                self.lg.addHandler(sysdl)
            else:
                # for logging to stdout & stderr
                ch = logging.StreamHandler()
                logFormat = logging.Formatter(("%(asctime)s|%(name)s|%(levelname)s|%(message)s"))
                ch.setFormatter(logFormat)
                self.lg.addHandler(ch)

        self.host = host
        self.port = port

        # create mqtt client id
        self.client_id = f"measure-{uuid.uuid4().hex}"

        # setup mqtt subscriber client
        self.mqttc = mqtt.Client(client_id=self.client_id)
        self.mqttc.will_set("measurement/status", json.dumps("Offline"), 2, retain=True)
        self.mqttc.on_message = self.on_message
        self.mqttc.on_connect = self.on_connect
        self.mqttc.on_disconnect = self.on_disconnect

        if use_threads:
            self.killer = tEvent()
            self.inq = Queue()
            self.outq = Queue()
        else:  # processes
            self.killer = multiprocessing.Event()
            self.inq = mQueue()
            self.outq = mQueue()

        self.lg.debug("Initialized.")

    def __enter__(self):
        """Enter the runtime context related to this object."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.disconnect()
        return False

    # send up a log message to the status channel
    def send_log_msg(self, record: logging.LogRecord):
        payload = {"level": record.levelno, "msg": record.msg}
        self.outq.put({"topic": "measurement/log", "payload": json.dumps(payload), "qos": 2})

    # The callback for when a PUBLISH message is received from the server.
    def on_message(self, client: mqtt.Client, userdata: typing.Any, msg):
        """runs when there's a message on"""
        try:
            self.inq.put(msg)  # put the message in the incomming queue
        except Exception as e:
            self.lg.error(f"Failure handing incomming message: {e}")

    # when client connects to broker
    def on_connect(self, client: mqtt.Client, userdata, flags, rc):
        self.lg.debug(f"mqtt_server connected to broker with result code {rc}")
        client.subscribe("measurement/#", qos=2)
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
        while not self.killer.is_set():
            to_send = self.outq.get()
            try:
                if to_send == "die":
                    break
                else:
                    self.mqttc.publish(**to_send)
            except Exception as e:
                self.lg.error(f"Error publishing message to broker: {e}")

    def start_loop(self) -> int:
        """spawn a thread and maintain the mqtt loop in there"""
        self.mqttc.connect_async(self.host, self.port)
        try:
            self.mqttc.loop_start()
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
        self.killer.set()
        self.outq.put("die")  # ask the out_relay to stop
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
        self.workers.append(threading.Thread(target=self.out_relay, daemon=True))
        self.workers[-1].start()

        # begin mqtt loop maintanince (blocks here)
        self.run_loop()

        return self.retcode

    def start(self):
        """starts the mqtt connection nonblockingly in two threads"""
        # start the outq handler thread (sends messages to the broker)
        self.workers.append(threading.Thread(target=self.out_relay, daemon=True))
        self.workers[-1].start()

        self.start_loop()  # TODO: see if we can find the thread here...

    def execute(self, executer: Executor):
        """run this via an external Executer"""
        pass


if __name__ == "__main__":
    sys.exit(MQTTClient().run())
