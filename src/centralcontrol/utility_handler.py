#!/usr/bin/env python3

import contextlib
import paho.mqtt.client as mqtt
import argparse
import json
import threading
import queue
import serial  # for monochromator
from pathlib import Path
import sys
import collections
import numpy as np
import time
import uuid

# for main loop & multithreading
import gi
from gi.repository import GLib

import logging
import logging.handlers

# for logging directly to systemd journal if we can
try:
    import systemd.journal
except ImportError:
    pass

# this boilerplate code allows this module to be run directly as a script
if (__name__ == "__main__") and (__package__ in [None, ""]):
    __package__ = "centralcontrol"
    # get the dir that holds __package__ on the front of the search path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from . import virt
from .motion import motion
from .pcb import Pcb
from centralcontrol import sourcemeter
from centralcontrol import illumination


class UtilityHandler(object):
    # for storing command messages as they arrive
    cmdq = queue.Queue()

    # for storing jobs to be worked on
    taskq = queue.Queue()

    # for outgoing messages
    outputq = queue.Queue()

    def __init__(self, mqtt_server_address="127.0.0.1", mqtt_server_port=1883):
        self.mqtt_server_address = mqtt_server_address
        self.mqtt_server_port = mqtt_server_port

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

        self.lg.debug("Initialized.")

    # The callback for when the client receives a CONNACK response from the server.
    def on_connect(self, client, userdata, flags, rc):
        self.lg.debug(f"Utility handler connected to broker with result code {rc}")

        # Subscribing in on_connect() means that if we lose the connection and
        # reconnect then subscriptions will be renewed.
        client.subscribe("cmd/#", qos=2)

    # The callback for when a PUBLISH message is received from the server.
    # this function must be fast and non-blocking to avoid estop service delay
    def handle_message(self, client, userdata, msg):
        self.cmdq.put_nowait(msg)  # pass this off for our worker to deal with

    # filters out all mqtt messages except
    # properly formatted command messages, unpacks and returns those
    # this function must be fast and non-blocking to avoid estop service delay
    def filter_cmd(self, mqtt_msg):
        result = {"cmd": ""}
        try:
            msg = json.loads(mqtt_msg.payload.decode())
        except Exception as e:
            msg = None
        if isinstance(msg, collections.abc.Iterable):
            if "cmd" in msg:
                result = msg
        return result

    # the manager thread decides if the command should be passed on to the worker or rejected.
    # immediagely handles estops itself
    # this function must be fast and non-blocking to avoid estop service delay
    def manager(self):
        while True:
            cmd_msg = self.filter_cmd(self.cmdq.get())
            self.lg.debug("New command message!")
            if cmd_msg["cmd"] == "estop":
                if cmd_msg["pcb_virt"] == True:
                    tpcb = virt.pcb
                else:
                    tpcb = Pcb
                try:
                    with tpcb(cmd_msg["pcb"], timeout=10) as p:
                        p.query("b")
                    self.lg.warning("Emergency stop command issued. Re-Homing required before any further movements.")
                except Exception as e:
                    emsg = "Unable to emergency stop."
                    self.lg.warning(emsg)
                    logging.exception(emsg)
            elif self.taskq.unfinished_tasks == 0:
                # the worker is available so let's give it something to do
                self.taskq.put_nowait(cmd_msg)
            elif self.taskq.unfinished_tasks > 0:
                self.lg.warning(f"Backend busy (task queue size = {self.taskq.unfinished_tasks}). Command rejected.")
            else:
                self.lg.debug(f"Command message rejected:: {cmd_msg}")
            self.cmdq.task_done()

    # asks for the current stage position and sends it up to /response
    def send_pos(self, mo):
        pos = mo.get_position()
        payload = {"pos": pos}
        payload = json.dumps(payload)
        output = {"destination": "response", "payload": payload}  # post the position to the response channel
        self.outputq.put(output)

    # work gets done here so that we don't do any processing on the mqtt network thread
    # can block and be slow. new commands that come in while this is working will just be rejected
    def worker(self):
        while True:
            task = self.taskq.get()
            self.lg.debug(f"New task: {task['cmd']} (queue size = {self.taskq.unfinished_tasks})")
            # handle pcb and stage virtualization
            stage_pcb_class = Pcb
            pcb_class = Pcb
            if "stage_virt" in task:
                if task["stage_virt"] == True:
                    stage_pcb_class = virt.pcb
            if "pcb_virt" in task:
                if task["pcb_virt"] == True:
                    pcb_class = virt.pcb
            try:  # attempt to do the task
                if task["cmd"] == "home":
                    self.lg.debug(f"Starting on {task['cmd']=}")
                    with stage_pcb_class(task["pcb"], timeout=5) as p:
                        mo = motion(address=task["stage_uri"], pcb_object=p)
                        mo.connect()
                        if task["force"] == True:
                            needs_home = True
                        else:
                            needs_home = False
                            for ax in ["1", "2", "3"]:
                                len_ret = p.query(f"l{ax}")
                                if len_ret == "0":
                                    needs_home = True
                                    break
                        if needs_home == True:
                            mo.home()
                            self.lg.log(29, "Stage calibration procedure complete.")
                            self.send_pos(mo)
                        else:
                            self.lg.log(29, "The stage is already calibrated.")
                    del mo
                    self.lg.debug(f"{task['cmd']=} complete!")

                # send the stage some place
                elif task["cmd"] == "goto":
                    with stage_pcb_class(task["pcb"], timeout=5) as p:
                        mo = motion(address=task["stage_uri"], pcb_object=p)
                        mo.connect()
                        mo.goto(task["pos"])
                        self.send_pos(mo)
                    del mo
                    self.lg.debug(f"{task['cmd']=} complete!")

                # handle any generic PCB command that has an empty return on success
                elif task["cmd"] == "for_pcb":
                    with pcb_class(task["pcb"], timeout=5) as p:
                        # special case for pixel selection to avoid parallel connections
                        if task["pcb_cmd"].startswith("s") and ("stream" not in task["pcb_cmd"]) and (len(task["pcb_cmd"]) != 1):
                            p.query("s")  # deselect all before selecting one
                        result = p.query(task["pcb_cmd"])
                    if result == "":
                        self.lg.debug(f"Command acknowledged: {task['pcb_cmd']}")
                    else:
                        self.lg.warning(f"Command {task['pcb_cmd']} not acknowleged with {result}")
                    self.lg.debug(f"{task['cmd']=} complete!")

                # get the stage location
                elif task["cmd"] == "read_stage":
                    with stage_pcb_class(task["pcb"], timeout=5) as p:
                        mo = motion(address=task["stage_uri"], pcb_object=p)
                        mo.connect()
                        self.send_pos(mo)
                    del mo
                    self.lg.debug(f"{task['cmd']=} complete!")

                # zero the mono
                elif task["cmd"] == "mono_zero":
                    if task["mono_virt"] == True:
                        self.lg.log(29, "0 GOTO virtually worked!")
                        self.lg.log(29, "1 FILTER virtually worked!")
                    else:
                        with serial.Serial(task["mono_address"], 9600, timeout=1) as mono:
                            mono.write("0 GOTO")
                            self.lg.log(29, mono.readline.strip())
                            mono.write("1 FILTER")
                            self.lg.log(29, mono.readline.strip())
                    self.lg.debug(f"{task['cmd']=} complete!")

                elif task["cmd"] == "spec":
                    sscfg = task["solarsim"]  # the solar sim configuration
                    sscfg["active_recipe"] = task["recipe"]
                    sscfg["intensity"] = task["intensity"]
                    self.lg.log(29, "Fetching solar sim spectrum...")
                    solarsim_class = illumination.factory(sscfg)  # use the class factory to get a solarsim class
                    ss = solarsim_class(**sscfg)  # instantiate the class
                    emsg = []
                    try:
                        with ss as connected_solarsim:  # use the context manager to manage connection and disconnection
                            conn_status = connected_solarsim.conn_status
                            connected_solarsim.intensity = 0  # let's make sure it's off
                            data = connected_solarsim.get_spectrum()
                            temps = connected_solarsim.last_temps
                    except Exception as e:
                        emsg.append(f"🔴 Solar sim comms failure: {e}")
                    else:  # no error, check the connection and disconnection status numbers
                        if conn_status < 0:  # check for unclean connection
                            emsg.append(f"🔴 Unable to complete connection to solar sim: {conn_status=}")
                        if ss.conn_status != -80:  # check for unclean disconnection
                            emsg.append(f"🔴 Unclean disconnection from solar sim")
                        if not isinstance(data, tuple) or len(data) != 2:  # check data shape
                            emsg.append(f"🔴 Spectrum data was malformed.")

                    # notify user
                    if len(emsg) > 0:
                        for badmsg in emsg:
                            self.lg.warning(badmsg)
                            logging.exception(badmsg)
                    else:
                        response = {}
                        response["data"] = data
                        response["timestamp"] = time.time()
                        output = {"destination": "calibration/spectrum", "payload": json.dumps(response)}
                        self.outputq.put(output)
                        self.lg.log(29, "🟢 Spectrum fetched sucessfully!")
                        self.lg.log(29, f"Found light source temperatures: {temps}")
                    self.lg.debug(f"{task['cmd']=} complete!")

                # device round robin commands
                elif task["cmd"] == "round_robin":
                    if len(task["slots"]) > 0:
                        with pcb_class(task["pcb"], timeout=5) as p:
                            p.query("iv")  # make sure the circuit is in I-V mode (not eqe)
                            p.query("s")  # make sure we're starting with nothing selected

                            smucfgs = task["smu"]  # a list of sourcemeter configurations
                            with contextlib.ExitStack() as stack:  # handles the proper cleanup of all the SMUs
                                # initialize and connect to all the sourcemeters in a way that will gracefully disconnect them later
                                smus = [stack.enter_context(sourcemeter.factory(smucfg)(**smucfg)) for smucfg in smucfgs]
                                for sm in smus:
                                    if "device_grouping" in task:
                                        sm.device_grouping = task["device_grouping"]
                                    # set up sourcemeter for the task
                                    if task["type"] == "current":
                                        if "current_limit" in sm.init_kwargs:
                                            i_lim = sm.init_kwargs["current_limit"]
                                        else:
                                            i_lim = 0.15
                                        sm.setupDC(sourceVoltage=True, compliance=i_lim, setPoint=0.0, senseRange="a", ohms=False)
                                    if task["type"] == "voltage":
                                        sm.setupDC(sourceVoltage=False, compliance=3, setPoint=0.0, senseRange="a", ohms=False)
                                    elif task["type"] == "rtd":
                                        sm.setupDC(sourceVoltage=False, compliance=3, setPoint=0.001, senseRange="f", ohms=True)
                                    elif task["type"] == "connectivity":
                                        sm.enable_cc_mode(True)

                                if task["type"] == "connectivity":
                                    self.lg.log(29, f"Checking connections. Only failures will be printed.")
                                    lo_side = False  # start with high side checking
                                # handle the all switches open case:
                                task["slots"].insert(0, "none")
                                task["pads"].insert(0, "none")
                                task["mux_strings"].insert(0, "s")
                                for i, slot in enumerate(task["slots"]):
                                    dev = task["pads"][i]
                                    if slot == "none":
                                        slot_words = "[Everything disconnected]"
                                    else:
                                        slot_words = f"[{slot}{dev:n}]"
                                    mux_string = task["mux_strings"][i]
                                    p.query(mux_string)  # select the device
                                    if slot == "none":
                                        smu_index = 0  # I guess we should just use smu[0] for the all switches open case
                                    else:
                                        smu_index = smus[0].which_smu(f"{slot}{int(dev)}".lower())  # figure out which smu owns the device
                                    if smu_index is None:
                                        smu_index = 0
                                        self.lg.warning("Assuming the first SMU is the right one")
                                    if smus[smu_index].idn != "disabled":
                                        if task["type"] == "current":
                                            m = smus[smu_index].measure()[0]
                                            status = int(m[3])
                                            in_compliance = (1 << 3) & status  # check compliance bit (3) in status word
                                            A = m[1]
                                            if in_compliance:
                                                self.lg.log(29, f"{slot_words} was in compliance")
                                            else:
                                                self.lg.log(29, f"{slot_words} shows {A:.8f} A")
                                        elif task["type"] == "voltage":
                                            m = smus[smu_index].measure()[0]
                                            status = int(m[3])
                                            in_compliance = (1 << 3) & status  # check compliance bit (3) in status word
                                            V = m[0]
                                            if in_compliance:
                                                self.lg.log(29, f"{slot_words} was in compliance")
                                            else:
                                                self.lg.log(29, f"{slot_words} shows {V:.6f} V")
                                        elif task["type"] == "rtd":
                                            m = smus[smu_index].measure()[0]
                                            ohm = m[2]
                                            status = int(m[4])
                                            in_compliance = (1 << 3) & status  # check compliance bit (3) in status word
                                            if not (in_compliance) and (ohm < 3000) and (ohm > 500):
                                                self.lg.log(29, f"{slot_words} could be a PT1000 RTD at {self.rtd_r_to_t(ohm):.1f} °C")
                                        elif task["type"] == "connectivity":
                                            good, val = smus[smu_index].do_contact_check(lo_side=lo_side)
                                            if not good:
                                                self.lg.log(29, f"🔴 {slot_words} has a bad low-side 4-wire connection")
                                    p.query(f"s{slot}0")  # disconnect the slot

                                # we need to do the loop again for cc check to check the high side
                                if task["type"] == "connectivity":
                                    lo_side = False
                                    for i, slot in enumerate(task["slots"]):
                                        dev = task["pads"][i]
                                        if slot == "none":
                                            slot_words = "[Everything disconnected]"
                                        else:
                                            slot_words = f"[{slot}{dev:n}]"
                                        mux_string = task["mux_strings"][i]
                                        p.query(mux_string)  # select the device
                                        if slot == "none":
                                            smu_index = 0  # I guess we should just use smu[0] for the all switches open case
                                        else:
                                            smu_index = smus[0].which_smu(f"{slot}{int(dev)}".lower())  # figure out which smu owns the device
                                        if smu_index is None:
                                            smu_index = 0
                                            self.lg.warning("Assuming the first SMU is the right one")
                                        if smus[smu_index].idn != "disabled":
                                            good, val = smus[smu_index].do_contact_check(lo_side=lo_side)
                                            if not good:
                                                self.lg.log(29, f"🔴 {slot_words} has a bad high-side 4-wire connection")
                                        p.query(f"s{slot}0")  # disconnect the slot

                                for sm in smus:
                                    if task["type"] == "connectivity":
                                        sm.enable_cc_mode(False)
                                    sm.outOn(False)
                            p.query("s")
                    self.lg.log(29, "Round robin task complete.")
            except Exception as e:
                self.lg.warning(e)
                logging.exception(e)
                try:
                    del mo  # ensure mo is cleaned up
                except:
                    pass

            # system health check
            if task["cmd"] == "check_health":
                if "pcb" in task:
                    self.lg.log(29, f"Checking controller@{task['pcb']}...")
                    try:
                        with pcb_class(task["pcb"], timeout=5) as p:
                            self.lg.log(29, "Controller connection initiated")
                            self.lg.log(29, f"Controller firmware version: {p.firmware_version}")
                            self.lg.log(29, f"Controller axes: {p.detected_axes}")
                            self.lg.log(29, f"Controller muxes: {p.detected_muxes}")
                    except Exception as e:
                        emsg = f"Could not talk to control box"
                        self.lg.warning(emsg)
                        logging.exception(emsg)

                if "smu" in task:
                    smucfgs = task["smu"]  # a list of sourcemeter configurations
                    for index, smucfg in enumerate(smucfgs):  # loop through the list of SMUs
                        if "address" in smucfg:
                            addrwords = f" at {smucfg['address']}"
                        else:
                            addrwords = ""
                        self.lg.log(29, f"Checking sourcemeter #{index}{addrwords}...")

                        sourcemeter_class = sourcemeter.factory(smucfg)  # use the class factory to get a sourcemeter class
                        sm = sourcemeter_class(**smucfg)  # instantiate the class
                        emsg = []
                        try:
                            with sm as connected_sourcemeter:  # use the context manager to manage connection and disconnection
                                conn_status = connected_sourcemeter.conn_status
                                smuidn = connected_sourcemeter.idn
                        except Exception as e:
                            emsg.append(f"🔴 Sourcemeter comms failure: {e}")
                        else:  # no error, check the connection and disconnection status numbers
                            if conn_status < 0:  # check for unclean connection
                                emsg.append(f"🔴 Unable to complete connection to SMU: {conn_status=}")
                            elif sm.conn_status != -80:  # check for unclean disconnection
                                emsg.append(f"🔴 Unclean disconnection from SMU")

                        # notify user
                        if len(emsg) > 0:
                            for badmsg in emsg:
                                self.lg.warning(badmsg)
                                logging.exception(badmsg)
                        else:
                            self.lg.log(29, f"🟢 Sourcemeter comms working correctly. Identifed as: {smuidn}")

                if "lia_address" in task:
                    self.lg.log(29, f"Checking lock-in@{task['lia_address']}...")
                    if task["lia_virt"] == True:
                        self.lg.log(29, f"Lock-in looks virtually great!")
                    else:
                        try:
                            with rm.open_resource(task["lia_address"], baud_rate=9600) as lia:
                                lia.read_termination = "\r"
                                self.lg.log(29, "Lock-in connection initiated")
                                idn = lia.query("*IDN?")
                                self.lg.log(29, f"Lock-in identification string: {idn.strip()}")
                        except Exception as e:
                            emsg = f"Could not talk to lock-in"
                            self.lg.warning(emsg)
                            logging.exception(emsg)

                if "mono_address" in task:
                    self.lg.log(29, f"Checking monochromator@{task['mono_address']}...")
                    if task["mono_virt"] == True:
                        self.lg.log(29, f"Monochromator looks virtually great!")
                    else:
                        try:
                            with rm.open_resource(task["mono_address"], baud_rate=9600) as mono:
                                self.lg.log(29, "Monochromator connection initiated")
                                qu = mono.query("?nm")
                                self.lg.log(29, f"Monochromator wavelength query result: {qu.strip()}")
                        except Exception as e:
                            emsg = f"Could not talk to monochromator"
                            self.lg.warning(emsg)
                            logging.exception(emsg)

                if "solarsim" in task:
                    sscfg = task["solarsim"]
                    recipe = task["recipe"]
                    intensity = task["intensity"]
                    self.lg.log(29, "Checking solar sim comms...")
                    solarsim_class = illumination.factory(sscfg)  # use the class factory to get a solarsim class
                    ss = solarsim_class(**sscfg)  # instantiate the class
                    emsg = []
                    try:
                        with ss as connected_solarsim:  # use the context manager to manage connection and disconnection
                            conn_status = connected_solarsim.conn_status
                            run_status = connected_solarsim.get_run_status()
                            ssidn = connected_solarsim.idn
                            if not isinstance(run_status, str):
                                emsg.append(f"🔴 Unable to complete solar sim status query {run_status=}")
                            else:
                                self.lg.log(29, f"Solar sim connection successful. Run Status = {run_status}")
                                return_code = connected_solarsim.activate_recipe(recipe)
                                if return_code == 0:
                                    self.lg.log(29, f'"{recipe}" recipe activated!')
                                    connected_solarsim.intensity = int(intensity)
                                    actual_intensity = connected_solarsim.intensity
                                    if actual_intensity == int(intensity):
                                        self.lg.log(29, f"{int(intensity)}% instensity set sucessfully!")
                                    else:
                                        emsg.append(f"🔴 Tried to set intensity to {int(intensity)}%, but it is {actual_intensity}%")
                                else:
                                    emsg.append(f"🔴 Unable to set recipe {recipe} with error {return_code}")
                    except Exception as e:
                        emsg.append(f"🔴 Solar sim comms failure: {e}")
                    else:  # no hard error, check the connection and disconnection status numbers
                        if conn_status < 0:  # check for unclean connection
                            emsg.append(f"🔴 Unable to complete connection to solar sim: {conn_status=}")
                        if ss.conn_status != -80:  # check for unclean disconnection
                            emsg.append(f"🔴 Unclean disconnection from solar sim")

                    # notify user
                    if len(emsg) > 0:
                        for badmsg in emsg:
                            self.lg.warning(badmsg)
                            logging.exception(badmsg)
                    else:
                        self.lg.log(29, f"🟢 Solarsim comms working correctly. Identifed as: {ssidn}")
                self.lg.debug(f"{task['cmd']=} complete!")

            self.taskq.task_done()

    # send up a log message to the status channel
    def send_log_msg(self, record):
        payload = {"log": {"level": record.levelno, "text": str(record.msg)}}
        payload = json.dumps(payload)
        output = {"destination": "status", "payload": payload}
        self.outputq.put(output)

    # thread that publishes mqtt messages on behalf of the worker and manager
    def sender(self, mqttc):
        while True:
            to_send = self.outputq.get()
            mqttc.publish(to_send["destination"], to_send["payload"], qos=2).wait_for_publish()
            self.outputq.task_done()

    # converts RTD resistance to temperature. set r0 to 100 for PT100 and 1000 for PT1000
    def rtd_r_to_t(self, r, r0=1000, poly=None):
        PTCoefficientStandard = collections.namedtuple("PTCoefficientStandard", ["a", "b", "c"])
        # Source: http://www.code10.info/index.php%3Foption%3Dcom_content%26view%3Darticle%26id%3D82:measuring-temperature-platinum-resistance-thermometers%26catid%3D60:temperature%26Itemid%3D83
        ptxIPTS68 = PTCoefficientStandard(+3.90802e-03, -5.80195e-07, -4.27350e-12)
        ptxITS90 = PTCoefficientStandard(+3.9083e-03, -5.7750e-07, -4.1830e-12)
        standard = ptxITS90  # pick an RTD standard

        noCorrection = np.poly1d([])
        pt1000Correction = np.poly1d([1.51892983e-15, -2.85842067e-12, -5.34227299e-09, 1.80282972e-05, -1.61875985e-02, 4.84112370e00])
        pt100Correction = np.poly1d([1.51892983e-10, -2.85842067e-08, -5.34227299e-06, 1.80282972e-03, -1.61875985e-01, 4.84112370e00])

        A, B = standard.a, standard.b

        if poly is None:
            if abs(r0 - 1000.0) < 1e-3:
                poly = pt1000Correction
            elif abs(r0 - 100.0) < 1e-3:
                poly = pt100Correction
            else:
                poly = noCorrection

        t = (-r0 * A + np.sqrt(r0 * r0 * A * A - 4 * r0 * B * (r0 - r))) / (2.0 * r0 * B)

        # For subzero-temperature refine the computation by the correction polynomial
        if r < r0:
            t += poly(r)
        return t

    def run(self):
        self.loop = GLib.MainLoop.new(None, False)

        # start the manager (decides what to do with commands from mqtt)
        threading.Thread(target=self.manager, daemon=True).start()

        # start the worker (does tasks the manger tells it to)
        threading.Thread(target=self.worker, daemon=True).start()

        # create mqtt client id
        self.client_id = f"utility-{uuid.uuid4().hex}"

        self.client = mqtt.Client(client_id=self.client_id)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.handle_message

        # connect to the mqtt server
        self.client.connect_async(self.mqtt_server_address, port=self.mqtt_server_port, keepalive=60)

        # start the sender (publishes messages from worker and manager)
        threading.Thread(target=self.sender, args=(self.client,), daemon=True).start()

        try:
            self.client.loop_forever()
        except Exception as e:
            self.lg.error(f"Unable to start message broker loop: {e}")


def main():
    parser = argparse.ArgumentParser(description="Utility handler")
    parser.add_argument("-a", "--address", type=str, default="127.0.0.1", const="127.0.0.1", nargs="?", help="ip address/hostname of the mqtt server")
    parser.add_argument("-p", "--port", type=int, default=1883, help="MQTT server port")
    args = parser.parse_args()

    u = UtilityHandler(mqtt_server_address=args.address, mqtt_server_port=args.port)
    u.run()  # blocks forever


if __name__ == "__main__":
    main()
