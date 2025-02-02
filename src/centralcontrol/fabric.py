#!/usr/bin/env python3

import collections
import concurrent.futures
import contextlib
import datetime
import hmac
import importlib.metadata
from collections import OrderedDict
import asyncio
import json
import multiprocessing
import sched
import signal
import threading
import time
import traceback
import typing
from typing import cast
from contextlib import contextmanager
from logging import Logger
from multiprocessing.queues import SimpleQueue as mQueue
from queue import SimpleQueue as Queue

import humanize
import numpy as np
from paho.mqtt.client import MQTTMessage
import centralcontrol.enums as en

# from slothdb.dbsync import SlothDBSync as SlothDB
import redis

# import redis_annex

from centralcontrol.mux481can import Mux481can
from centralcontrol import virt
from centralcontrol.illumination import LightAPI
from centralcontrol.illumination import factory as ill_fac
from centralcontrol.logstuff import get_logger
from centralcontrol.mc import MC
from centralcontrol.motion import Motion
from centralcontrol.mppt import MPPT
from centralcontrol.mqtt import MQTTClient
from centralcontrol.sourcemeter import SourcemeterAPI
from centralcontrol.sourcemeter import factory as smu_fac
from centralcontrol.dblink import DBLink
from centralcontrol import __version__ as backend_ver
from centralcontrol.datalogger import DataLogger


class DataHandler(object):
    """Handler for measurement data."""

    kind: str = ""
    illuminated_sweep: bool | None = None
    dbputter: None | typing.Callable[[list[tuple[float, float, float, int]], None | int], int] = None

    def __init__(self, pixel: dict, outq: mQueue):
        self.pixel = pixel
        self.outq = outq

    def handle_data(self, data: list[tuple[float, float, float, int]], dodb: bool = True) -> int:
        result = 0
        if self.illuminated_sweep is None:
            sweep_string = ""
        elif self.illuminated_sweep:
            sweep_string = "light"
        else:
            sweep_string = "dark"
        payload = {"data": data, "pixel": self.pixel, "sweep": sweep_string}
        if self.dbputter and dodb:
            result = self.dbputter(data, None)
        self.outq.put({"topic": f"data/raw/{self.kind}", "payload": json.dumps(payload), "qos": 2})
        return result

    def handle_logger_data(self, channel:int, t: float, name: str, value: float, unit: str, dodb: bool = True):
        result = 0

        payload = {}
        payload["num"] = channel
        payload["time"] = t
        payload["name"] = name
        payload["value"] = value
        payload["unit"] = unit

        if self.dbputter and dodb:
            result = self.dbputter(payload, None)

        self.outq.put({"topic": f"data/raw/{self.kind}", "payload": json.dumps(payload), "qos": 2})
        return result

class Fabric(object):
    """High level experiment control logic"""

    mem_db_url: str = "redis://"

    # process killer signal
    pkiller = multiprocessing.Event()

    # bad connections ask blocker
    bc_response = multiprocessing.Event()

    # special message output queue so that messages can be sent from other processes
    # poutq = multiprocessing.SimpleQueue()
    outq = multiprocessing.SimpleQueue()

    # mqtt connection details
    # set mqttargs["host"] externally before calling run() to use mqtt comms
    mqttargs = {"host": None, "port": 1883}
    hk = "gosox".encode()

    # threads/processes
    workers: list[threading.Thread | multiprocessing.Process] = []

    exitcode: int = 0

    def __init__(self, mem_db_url: str | None = None):
        if mem_db_url:
            self.mem_db_url = mem_db_url

        self.lg = get_logger(".".join([__name__, type(self).__name__]))  # setup logging

        self.lg.debug("Initialized.")

    def do_cleanup_stuff(self, inq: Queue, dbl: DBLink, signal: signal.Signals):
        if self.lg:
            self.lg.debug(f"We caught a {signal.name}. Handling...")

        # stop awaiting for new messages
        dbl.stop_listening()

        # ask the main loop to break with a "die" message
        inq.put("die")

    def run(self) -> int:
        """runs the measurement server. blocks forever"""
        # aloop = asyncio.new_event_loop()
        inq = Queue()  # queue for incomming comms messages TODO: switch this to asyncio.Queue

        commcls = None
        comms_args = None
        if self.mqttargs["host"] is not None:
            commcls = MQTTClient
            comms_args = self.mqttargs
            comms_args["parent_outq"] = self.outq
            comms_args["parent_inq"] = inq
        assert commcls is not None, f"{commcls=}"
        assert comms_args is not None, f"{comms_args=}"
        with commcls(**comms_args):  # for mqtt comms
            with redis.Redis.from_url(self.mem_db_url) as r:
                with DBLink(r, inq, self.lg) as dbl:  # manager for the mem-db inq listener
                    # handle SIGTERM and SIGINT gracefully by asking the runners to clean themselves up
                    signal.signal(signal.SIGTERM, lambda _, __: self.do_cleanup_stuff(inq, dbl, signal.SIGTERM))
                    signal.signal(signal.SIGINT, lambda _, __: self.do_cleanup_stuff(inq, dbl, signal.SIGINT))

                    # run the message handler, blocking right here forever
                    # async with asyncio.TaskGroup() as tg:  # TODO: use this when we get 3.11
                    async def do_gather():
                        to_gather = []
                        to_gather.append(asyncio.to_thread(self.msg_handler, inq))
                        to_gather.append(dbl.inq_handler())
                        await asyncio.gather(*to_gather)

                    asyncio.run(do_gather())

        self.lg.debug("Graceful exit achieved")
        return self.exitcode

    def on_future_done(self, future: concurrent.futures.Future):
        """callback for when the future's execution has concluded"""
        self.pkiller.clear()  # unset the process killer signal since it just ended
        self.bc_response.clear()  # make sure this is reset too
        future_exception = future.exception()  # check if the process died because of an exception
        if future_exception:
            self.lg.error(f"Process failed: {repr(future_exception)}")
            # log the exception's whole call stack for debugging
            tb = traceback.TracebackException.from_exception(future_exception)
            self.lg.debug("".join(tb.format()))
        self.outq.put({"topic": "measurement/status", "payload": json.dumps("Ready"), "qos": 2, "retain": True})

    def msg_handler(self, inq: Queue[list | str | MQTTMessage]):
        """handle new messages as they come in from comms, the main program loop lives here"""
        future = None  # represents a long-running task
        # decode_topics = ["measurement/run", "util"]  # messages posted to these channels need their payloads decoded
        with concurrent.futures.ProcessPoolExecutor(max_workers=1) as exicuter:
            try:  # this try/except block is for catching keyboard interrupts and then asking the main loop to break
                while True:  # main program loop
                    try:  # this high level try/except block lets the main loop keep running through programming errors
                        msg = inq.get()  # mostly execution sits right here waiting to be told what to do
                        if isinstance(msg, str):
                            if msg == "die":
                                break
                        elif isinstance(msg, MQTTMessage):
                            topic = msg.topic
                            if isinstance(topic, str):
                                channel = topic.split("/")
                                rootchan = channel.pop(0)

                                # unpack json payloads
                                if (topic == "measurement/run") or (rootchan == "cmd"):
                                    request = json.loads(msg.payload.decode())
                                else:
                                    request = None

                                # do something
                                if rootchan == "measurement":
                                    if channel == ["quit"]:
                                        self.lg.debug("Ending because of quit message")
                                        break
                                    elif channel == ["stop"]:
                                        self.stop_process(future)
                                    elif channel == ["run"]:
                                        pass  # handled by memdb
                                        # future = self.submit_for_execution(exicuter, future, self.do_run, request)
                                elif rootchan == "cmd":
                                    if channel == ["util"]:  # previously utility handler territory
                                        assert request is not None, f"{request is not None=}"
                                        if "cmd" in request:
                                            if request["cmd"] == "estop":
                                                self.estop(request)  # this gets done now instead of being done in a new process
                                            else:
                                                future = self.submit_for_execution(exicuter, future, self.utility_handler, request)
                                        elif request == "unblock":
                                            self.bc_response.set()  # unblock waiting for a response from the frontend
                        elif isinstance(msg, tuple) and len(msg) == 3:  # from memdb
                            channel, stream_id, payload = msg
                            if channel == b"runs":
                                rid = stream_id
                                self.lg.debug(f"Got new run start with id: {rid.decode()}")
                                if (future == None) or future.done():  # TODO: figure out why we can get two runs at once
                                    future = self.submit_for_execution(exicuter, future, self.do_run, {"runid": rid} | json.loads(payload[b"json"]))
                                else:
                                    self.lg.debug(f"Run start ignored.")

                        else:
                            self.lg.debug(f"Unknown message type in inq: {type(msg)}")

                    except Exception as e:
                        self.lg.error(f"Runtime exception: {repr(e)}")
                        # log the exception's whole call stack for debugging
                        tb = traceback.TracebackException.from_exception(e)
                        self.lg.debug("".join(tb.format()))

                        # tell the front end we're ready again after the crash
                        self.outq.put({"topic": "measurement/status", "payload": json.dumps("Ready"), "qos": 2, "retain": True})
            except KeyboardInterrupt:
                self.lg.debug("Ending gracefully because of SIGINT-like signal")
                inq.put("die")  # ask the main loop to break

            # the main program loop as exited, clean things up
            self.outq.put("die")  # end the output queuq handler
            self.stop_process(future)
        self.lg.debug("Message handler stopped")

    def utility_handler(self, task: dict):
        """handles various utility requests"""
        # catch all the virtual cases right here
        ThisStageMC = MC
        ThisMC = MC

        if "mc_virt" in task:
            if task["mc_virt"] == True:
                ThisMC = virt.FakeMC

        if "stage_virt" in task:
            if task["stage_virt"] == True:
                ThisStageMC = virt.FakeMC
            else:
                ThisStageMC = ThisMC

        if "cmd" in task:
            cmd = task["cmd"]
            self.lg.debug(f"Starting on {cmd=}")

            if cmd == "home":
                self.home_stage(task, ThisStageMC)
            elif cmd == "goto":
                self.util_goto(task, ThisStageMC)
            elif cmd == "read_stage":
                self.util_read_stage(task, ThisStageMC)
            elif cmd == "for_pcb":
                self.util_mc_cmd(task, ThisMC)
            elif cmd == "spec":
                self.util_spectrum(task)
            elif cmd == "check_health":
                self.util_check_health(task, ThisMC, ThisStageMC)
            elif cmd == "round_robin":
                self.util_round_robin(task, ThisMC)

            self.lg.debug(f"{cmd=} complete!")

    @staticmethod
    def get_pad_rs(mc: MC | virt.FakeMC, sms: list[SourcemeterAPI], pads: list[int], slots: list[str], smuis: list[int], remap=None) -> list[dict]:
        """get a list of resistance values for all the connection pads of a given device list"""
        conns = []  # holds the connection info
        if len(slots) > 0:
            hconns = []  # holds the hi side connection info
            lconns = []  # holds the lo side connection info
            for i in range(len(slots)):
                # for slot, pad in zip(slots, pads):  # hi-side lines
                line = {}
                line["slot"] = slots[i]
                if slots[i] == "OFF":
                    pad = "HI"
                    dlp = f"{0:010}"
                else:
                    pad = pads[i]
                    if remap:  # handle special mux mapping
                        himap = remap[f"{(slots[i], pad)}"][0]
                        for hignum, higroup in enumerate(himap):
                            pintot = 0
                            line = {}
                            for pin in higroup:
                                pintot += 2**pin
                            line["slot"] = slots[i]
                            if pintot == 0:
                                line["pad"] = "HI"
                            else:
                                if len(himap) > 1:
                                    line["pad"] = f"{pad}H{hignum}"
                                else:
                                    line["pad"] = f"{pad}"
                            line["dlp"] = f"{(pintot):010}"
                            line["smi"] = smuis[i]
                            hconns.append(line)
                        lomap = remap[f"{(slots[i], pad)}"][1]
                        for lognum, logroup in enumerate(lomap):
                            pintot = 0
                            line = {}
                            for pin in logroup:
                                pintot += 2**pin
                            line["slot"] = slots[i]
                            if pintot == 0:
                                line["pad"] = "LO"
                            else:
                                if len(lomap) > 1:
                                    line["pad"] = f"{pad}L{lognum}"
                                else:
                                    line["pad"] = f"{pad}L"
                            line["dlp"] = f"{(pintot):010}"
                            line["smi"] = smuis[i]
                            lconns.append(line)
                        continue
                    elif pad == 0:
                        dlp = f"{0:010}"
                    else:
                        dlp = f"{(1<<(7+pad)):010}"
                line["pad"] = pad
                line["dlp"] = dlp  # use direct latch programming for the odd mux configs here
                line["smi"] = smuis[i]
                hconns.append(line)

            # lo side stuff
            # uslots = list(set(slots))  # unique substrates
            uidx = [slots.index(x) for x in set(slots)]  # indicies of unique slots
            lo_side_mux_strings = [("TOP", f"{(1<<0):010}"), ("BOT", f"{(1<<1):010}")]
            for i in uidx:
                for pad, sel in lo_side_mux_strings:
                    line = {}
                    line["slot"] = slots[i]
                    if slots[i] == "OFF":
                        line["pad"] = "LO"
                        line["dlp"] = f"{0:010}"
                        line["smi"] = smuis[i]
                        lconns.append(line)
                        break
                    else:
                        if remap:
                            continue  # we've already filled the non-off lconns
                        line["pad"] = pad
                        line["dlp"] = sel
                        line["smi"] = smuis[i]
                        # line["smi"] = SourcemeterAPI.which_smu(device_grouping, [uslot, 1])
                        # if line["smi"] is None:
                        #    # if the smu isn't registered in the config under pad# 1, try pad# 0
                        #    line["smi"] = SourcemeterAPI.which_smu(device_grouping, [uslot, 0])
                        lconns.append(line)

            # remove duplicated tests (there should only be dups for the lconns, but we'll do both here anyway to be extra sure)
            lsearch_list = [(x["slot"], x["dlp"]) for x in lconns]
            hsearch_list = [(x["slot"], x["dlp"]) for x in hconns]
            uidxl = [lsearch_list.index(x) for x in OrderedDict((x, True) for x in lsearch_list).keys()]
            uidxh = [hsearch_list.index(x) for x in OrderedDict((x, True) for x in hsearch_list).keys()]
            lconns = [lconns[i] for i in uidxl]
            hconns = [hconns[i] for i in uidxh]

            Fabric.select_pixel(mc)  # ensure we start with devices all deselected

            # ccmode setup leaves us in hi-side checking mode, so we do that first
            for sm in sms:
                sm.enable_cc_mode(True)

            last_slot = None
            for line in hconns:
                this_slot = line["slot"]
                if last_slot and (last_slot != this_slot) and (last_slot != "OFF"):
                    Fabric.select_pixel(mc, [(last_slot, 0)])  # make sure the last slot is cleaned up
                Fabric.select_pixel(mc, [(line["slot"], line["dlp"])])
                line["data"] = sms[line["smi"]].do_contact_check(False)
                last_slot = this_slot
            conns += hconns

            for line in lconns:
                this_slot = line["slot"]
                if last_slot and (last_slot != this_slot) and (last_slot != "OFF"):
                    Fabric.select_pixel(mc, [(last_slot, 0)])  # make sure the last slot is cleaned up
                Fabric.select_pixel(mc, [(line["slot"], line["dlp"])])
                line["data"] = sms[line["smi"]].do_contact_check(True)
                last_slot = this_slot
            conns += lconns

            # disable cc mode
            for sm in sms:
                sm.enable_cc_mode(False)

            Fabric.select_pixel(mc)  # ensure we end with devices all deselected

        return conns

    def util_round_robin(self, task: dict, AnMC: type[MC] | type[virt.FakeMC]):
        """handles message from the frontend requesting a round robin-type thing"""
        slots = task["slots"]
        pads = task["pads"]
        dev_layouts = task["dev_layouts"]
        layouts = task["layouts"]
        dev_grp = task["group_order"]
        user_labels = task["labels"]
        ms = [(slot, pad) for slot, pad in zip(slots, pads)]

        dev_dicts = []
        for i in range(len(slots)):
            dev_dict = {}
            dev_dict["slot"] = slots[i]
            dev_dict["pad"] = pads[i]
            dev_dict["layout"] = dev_layouts[i]
            dev_dict["user_label"] = user_labels[i]
            dev_dict["smui"] = SourcemeterAPI.which_smu(dev_grp, [slots[i], pads[i]])  # figure out which smu owns the device
            dev_dicts.append(dev_dict)
        smuis = [dd["smui"] for dd in dev_dicts]

        # inject the no connect case
        slots.insert(0, "OFF")
        pads.insert(0, 0)
        smuis.insert(0, 0)
        ms.insert(0, ("OFF", 0))

        if len(dev_dicts) > 0:
            with contextlib.ExitStack() as stack:  # handles the proper cleanup of the hardware
                mc = stack.enter_context(AnMC(task["mc"], timeout=5))
                db = stack.enter_context(redis.Redis.from_url(self.mem_db_url))
                dbl = DBLink(db)
                smus = [stack.enter_context(smu_fac(smucfg)(**smucfg)) for smucfg in task["smus"]]
                config = json.loads(db.xrange("conf_as", task["conf_id"], task["conf_id"])[0][1][b"json"])
                suid = db.xadd("setups", fields={"json": json.dumps(config["setup"])}, maxlen=100, approximate=True).decode()

                if "remap" in config["mux"]:
                    remap = {}
                    for line in config["mux"]["remap"]:
                        remap[f"{(line[0], line[1])}"] = (line[2], line[3])
                else:
                    remap = None

                lu = dbl.registerer(dev_dicts, suid, smus, layouts)
                Fabric.select_pixel(mc)  # ensure we start with devices all deselected
                if task["type"] == "connectivity":
                    rs = Fabric.get_pad_rs(mc, smus, pads, slots, smuis, remap=remap)
                    for r in rs:
                        if r["slot"] in lu["slots"]:  # make sure we don't try to register OFF
                            to_upsert = {}
                            to_upsert["substrate_id"] = lu["substrate_ids"][lu["slots"].index(r["slot"])]
                            to_upsert["setup_slot_id"] = lu["slot_ids"][lu["slots"].index(r["slot"])]
                            to_upsert["pad_name"] = str(r["pad"])
                            to_upsert["pass"] = r["data"][0]
                            to_upsert["r"] = r["data"][1]
                            assert isinstance(db.rpush("contact_checks", json.dumps(to_upsert)), int), "Failure writing to db"
                    self.lg.debug(repr(rs))
                    for line in rs:
                        if not line["data"][0]:
                            name = f'{line["slot"]}-{line["pad"]}'
                            filler = " "
                            self.lg.log(29, f"🔴 The {name:{filler}<6} pad has a connection fault")
                else:
                    for sm in smus:
                        if task["type"] == "current":
                            sm.setupDC(sourceVoltage=True, compliance=sm.current_limit, setPoint=0.0, senseRange="a", ohms=False)
                        if task["type"] == "voltage":
                            sm.setupDC(sourceVoltage=False, compliance=sm.voltage_limit, setPoint=0.0, senseRange="a", ohms=False)
                        elif task["type"] == "rtd":
                            sm.setupDC(sourceVoltage=False, compliance=sm.voltage_limit, setPoint=0.001, senseRange="f", ohms=True)
                    for i, slot in enumerate(slots):
                        pad = pads[i]
                        if slot == "OFF":
                            slot_words = "[Everything disconnected]"
                        else:
                            slot_words = f"[{slot}{pad:n}]"
                        Fabric.select_pixel(mc, [ms[i]])  # select the device
                        smui = smuis[i]
                        if smus[smui].idn != "disabled":
                            if task["type"] == "current":
                                m = smus[smui].measure()[0]
                                status = int(m[3])
                                in_compliance = (1 << 3) & status  # check compliance bit (3) in status word
                                A = m[1]
                                if in_compliance:
                                    self.lg.log(29, f"{slot_words} was in compliance")
                                else:
                                    self.lg.log(29, f"{slot_words} shows {A:.8f} A")
                            elif task["type"] == "voltage":
                                m = smus[smui].measure()[0]
                                status = int(m[3])
                                in_compliance = (1 << 3) & status  # check compliance bit (3) in status word
                                V = m[0]
                                if in_compliance:
                                    self.lg.log(29, f"{slot_words} was in compliance")
                                else:
                                    self.lg.log(29, f"{slot_words} shows {V:.6f} V")
                            elif task["type"] == "rtd":
                                m = smus[smui].measure()[0]
                                ohm = m[2]
                                status = int(m[4])  # type: ignore
                                in_compliance = (1 << 3) & status  # check compliance bit (3) in status word
                                if not (in_compliance) and (ohm < 3000) and (ohm > 500):
                                    self.lg.log(29, f"{slot_words} could be a PT1000 RTD at {self.rtd_r_to_t(ohm):.1f} °C")
                        if slot != "none":
                            Fabric.select_pixel(mc, [(slot, 0)])  # disconnect the slot
                    for sm in smus:
                        sm.enable_cc_mode(False)
                Fabric.select_pixel(mc)  # disconnect everyone
        self.lg.log(29, "Round robin task complete.")

    def util_check_health(self, task: dict, AnMC: type[MC] | type[virt.FakeMC], AStageMC: type[MC] | type[virt.FakeMC]):
        """handles message from the frontend requesting a utility health check"""
        if "mc" in task:
            self.lg.log(29, f'Checking MC@{task["mc"]}...')
            try:
                with AnMC(task["mc"], timeout=5) as mc:
                    self.lg.debug(f"MC firmware version: {mc.firmware_version}")
                    self.lg.debug(f"MC axes: {mc.detected_axes}")
                    self.lg.debug(f"MC muxes: {mc.detected_muxes}")
                    self.lg.log(29, f"🟢 PASS!")
            except Exception as e:
                self.lg.warning(f"🔴 FAIL: {repr(e)}")
                # log the exception's whole call stack trace for debugging
                tb = traceback.TracebackException.from_exception(e)
                self.lg.debug("".join(tb.format()))
        if "smus" in task:
            smucfgs = task["smus"]  # a list of sourcemeter configurations
            for smucfg in smucfgs:  # loop through the list of SMU configurations
                address = smucfg["address"]
                self.lg.log(29, f"Checking SMU@{address}...")
                try:
                    with smu_fac(smucfg)(**smucfg) as sm:
                        smuidn = sm.idn
                        conn_status = sm.conn_status
                        if smuidn and (conn_status >= 0):
                            self.lg.log(29, f"🟢 PASS!")
                        else:
                            self.lg.warning(f"🔴 FAIL!")
                            self.lg.debug(f"{smuidn=}")
                            self.lg.debug(f"{conn_status=}")
                except Exception as e:
                    self.lg.warning(f"🔴 FAIL: {repr(e)}")
                    # log the exception's whole call stack trace for debugging
                    tb = traceback.TracebackException.from_exception(e)
                    self.lg.debug("".join(tb.format()))
        if "solarsim" in task:
            self.lg.log(29, f'Checking Solar Sim @{task["solarsim"]["address"]}...')
            try:
                with ill_fac(task["solarsim"])(**task["solarsim"]) as ss:
                    conn_status = ss.conn_status
                    run_status = ss.get_run_status()
                    if (run_status in ("running", "finished")) and (conn_status >= 0):
                        self.lg.log(29, f"🟢 PASS!")
                    else:
                        self.lg.warning(f"🔴 FAIL!")
                        self.lg.debug(f"{run_status=}")
                        self.lg.debug(f"{conn_status=}")
            except Exception as e:
                self.lg.warning(f"🔴 FAIL: {repr(e)}")
                # log the exception's whole call stack trace for debugging
                tb = traceback.TracebackException.from_exception(e)
                self.lg.debug("".join(tb.format()))
        # TODO: stage: check axes, lengths and homing
        self.lg.log(29, "Health check complete")

    def util_spectrum(self, task):
        """handles message from the frontend requesting a utility spectrum fetch"""
        sscfg = task["solarsim"]  # the solar sim configuration
        sscfg["active_recipe"] = task["recipe"]
        sscfg["intensity"] = task["intensity"]
        self.lg.log(29, "Fetching solar sim spectrum...")
        emsg = []
        data = None
        temps = None
        try:
            with ill_fac(sscfg)(**sscfg) as ss:  # init and connect to solar sim
                conn_status = ss.conn_status
                if conn_status >= 0:
                    ss.intensity = 0  # let's make sure it's off
                    if hasattr(ss, "get_spectrum"):
                        data = ss.get_spectrum()
                        temps = ss.last_temps
                        if not isinstance(data, tuple) or len(data) != 2:  # check data shape
                            data = None
                            emsg.append(f"🔴 Spectrum data was malformed.")
                    else:
                        self.lg.log(29, "🟢 Spectrum fetching not supported for this light source.")
                        temps = ss.get_temperatures()
                else:
                    emsg.append(f"🔴 Unable to complete connection to solar sim: {conn_status=}")
        except Exception as e:
            self.lg.warning(f"🔴 Solar sim comms failure: {repr(e)}")
            # log the exception's whole call stack trace for debugging
            tb = traceback.TracebackException.from_exception(e)
            self.lg.debug("".join(tb.format()))
        else:  # no exception, check the disconnection status number
            if ss.conn_status != -80:  # check for unclean disconnection
                emsg.append(f"🔴 Unclean disconnection from solar sim")

        # notify user of anything strange
        for badmsg in emsg:
            self.lg.warning(badmsg)

        # send up spectrum
        if data:
            self.lg.log(29, "🟢 Spectrum fetched sucessfully!")
            response = {}
            response["data"] = data
            response["timestamp"] = time.time()
            self.outq.put({"topic": "calibration/spectrum", "payload": json.dumps(response), "qos": 2})

        if temps:
            self.lg.log(29, f"Light source temperatures: {temps}")

    def util_mc_cmd(self, task: dict, AnMC: type[MC] | type[virt.FakeMC]):
        """utility function for mc direct interaction"""
        with AnMC(task["mc"], timeout=5) as mc:
            # special case for pixel selection to avoid parallel connections
            if task["mc_cmd"].startswith("s") and ("stream" not in task["mc_cmd"]) and (len(task["mc_cmd"]) != 1):
                mc.query("s")  # deselect all before selecting one
            result = mc.query(task["mc_cmd"])
            if result == "":
                self.lg.debug(f"Command acknowledged: {task['pcb_cmd']}")
            else:
                self.lg.warning(f"Command {task['pcb_cmd']} not acknowleged with {result}")

    def util_goto(self, task: dict, AnMC: type[MC] | type[virt.FakeMC]):
        """utility function to send the stage somewhere"""
        with AnMC(task["mc"], timeout=5) as mc:
            mo = Motion(address=task["stage_uri"], pcb_object=mc)
            assert mo.connect() == 0, f"{(mo.connect() == 0)=}"  # make connection to motion system
            mo.goto(task["pos"])
            self.send_pos(mo)

    def util_read_stage(self, task: dict, AnMC: type[MC] | type[virt.FakeMC]):
        """utility function to send the stage's position up to the front end"""
        with AnMC(task["mc"], timeout=5) as mc:
            mo = Motion(address=task["stage_uri"], pcb_object=mc)
            assert mo.connect() == 0, f"{(mo.connect() == 0)=}"  # make connection to motion system
            self.send_pos(mo)

    def home_stage(self, task: dict, AnMC: type[MC] | type[virt.FakeMC]):
        """homes the stage"""
        with AnMC(task["mc"], timeout=5) as mc:
            mo = Motion(address=task["stage_uri"], pcb_object=mc)
            assert mo.connect() == 0, f"{(mo.connect() == 0)=}"  # make connection to motion system
            if task["force"] == True:
                needs_home = True
            else:
                needs_home = False
                for ax in ["1", "2", "3"]:
                    len_ret = mc.query(f"l{ax}")
                    if len_ret == "0":
                        needs_home = True
                        break
            if needs_home == True:
                mo.home()
                self.lg.log(29, "Stage calibration procedure complete.")
                self.send_pos(mo)
            else:
                self.lg.log(29, "The stage is already calibrated.")

    def send_pos(self, mo: Motion):
        """asks the motion controller for the current stage position and sends it up to the frontend"""
        pos = mo.get_position()
        self.outq.put({"topic": "status", "payload": json.dumps({"pos": pos}), "qos": 2})

    def submit_for_execution(self, exicuter: concurrent.futures.Executor, future_past: None | concurrent.futures.Future, callabale: typing.Callable, /, *args, **kwargs) -> concurrent.futures.Future:
        """submits a task for execution by an executor, sets up a callback for when it's done and updates status for front end"""
        if future_past and future_past.running():
            self.lg.warning("Request denied. The backend is currently busy.")
            ret = future_past
        else:
            future = exicuter.submit(self.future_wrapper, callabale, *args, **kwargs)
            future.add_done_callback(self.on_future_done)
            self.outq.put({"topic": "measurement/status", "payload": json.dumps("Busy"), "qos": 2, "retain": True})
            ret = future
        return ret

    def future_wrapper(self, callable: typing.Callable, /, *args, **kwargs) -> typing.Any:
        """wraps a function call that will be scheduled for execution"""
        # signal handlers needed here because this runs as its own process
        # which seems to get signals independantly from the main one
        signal.signal(signal.SIGTERM, lambda _, __: self.pkiller.set())
        signal.signal(signal.SIGINT, lambda _, __: self.pkiller.set())
        return callable(*args, **kwargs)

    def stop_process(self, future: concurrent.futures.Future | None):
        """Abort the running process with increasing meanness until success"""
        self.lg.debug("Stopping process")

        if isinstance(future, concurrent.futures.Future) and future.running():
            self.lg.debug("Setting process killer")
            self.bc_response.set()  # unblock if we're waiting for a bad connection response
            self.pkiller.set()  # ask extremely nicely for the process to come to conclusion
            concurrent.futures.wait([future], timeout=10)
            if not future.running():
                self.lg.log(29, "Request to stop completed!")
            else:
                future.cancel()  # politely tell the process to end
                concurrent.futures.wait([future], timeout=10)
                if not future.running():
                    self.lg.log(29, "Forceful request to stop completed!")
                else:
                    self.lg.warning("Unable to stop the process!")
        else:
            self.lg.log(29, "Nothing to stop. Measurement server is idle.")

    def do_run(self, request):
        """handle a request published to the 'run' topic"""
        if "runid" in request:
            self.lg.log(29, "Starting run...")
            self.standard_routine([[{}]], request)
            self.lg.log(29, "Run complete!")

        elif "rundata" in request:  # TODO: remove this legacy codde path
            rundata = request["rundata"]
            if "digest" in rundata:
                remotedigest_str: str = rundata.pop("digest")
                theirdigest = bytes.fromhex(remotedigest_str.removeprefix("0x"))
                jrundatab = json.dumps(rundata).encode()
                mydigest = hmac.digest(self.hk, jrundatab, "sha1")
                if theirdigest == mydigest:
                    if "args" in rundata:
                        args = rundata["args"]
                        if "enable_iv" in args:
                            if args["enable_iv"] == True:
                                if "slots" in request:  # shoehorn in unvalidated slot info loaded from a tsv file
                                    rundata["slots"] = request["slots"]
                                self.lg.log(29, "Starting run...")
                                things_to_measure = self.get_things_to_measure(rundata)
                                self.standard_routine(things_to_measure, rundata)
                                self.lg.log(29, "Run complete!")

    @staticmethod
    @contextmanager
    def measurement_context(mc: MC | virt.FakeMC, ss: LightAPI, smus: list[SourcemeterAPI], outq: Queue | mQueue, db: redis.Redis, rid: int):
        """context to ensure we're properly set up and then properly cleaned up"""
        # ensure we start with the light off
        # ss.apply_intensity(0)  # overrides barrier

        # ensure we start with the outputs off
        for smu in smus:
            smu.outOn(False)

        # ensure we start with devices all deselected
        Fabric.select_pixel(mc)

        try:
            yield None
        finally:
            # ensure we leave the light off
            ss.apply_intensity(0)  # overrides barrier

            # ensure we leave the outputs off
            for smu in smus:
                smu.outOn(False)

            # ensure we don't leave any devices connected
            Fabric.select_pixel(mc)

            outq.put({"topic": "progress", "payload": json.dumps({"text": "Done!", "fraction": 1}), "qos": 2})
            outq.put({"topic": "plotter/live_devices", "payload": json.dumps([]), "qos": 2, "retain": True})

            db.xadd("completed_runs", fields={"id": rid}, maxlen=100, approximate=True)

    def standard_routine(self, run_queue: list[list[dict]], request: dict) -> None:
        """perform the normal measurement routine on a given list of pixels"""

        # int("checkerberrycheddarchew")  # force crash for testing

        with redis.Redis.from_url(self.mem_db_url) as db:
            dbl = DBLink(db)
            if "runid" in request:
                rid = request["runid"].decode()
                conf_a_id = request["conf_a_id"]
                conf_b_id = request["conf_b_id"]
                rq_id = request["rq_id"]
                # notify of this backend's software version
                db.xadd("backend_vers", fields={rid: backend_ver}, maxlen=100, approximate=True)
                config = json.loads(db.xrange("conf_as", conf_a_id, conf_a_id)[0][1][b"json"])
                args = json.loads(db.xrange("conf_bs", conf_b_id, conf_b_id)[0][1][b"json"])
                run_queue = json.loads(db.xrange("runqs", rq_id, rq_id)[0][1][b"json"])
            else:  # TODO: remove this legacy code path
                rid = 1  # HACK a run id for testing
                if "config" in request:
                    config = request["config"]
                else:
                    config = {}

                if "args" in request:
                    args = request["args"]
                else:
                    args = {}

            if self.pkiller.is_set():
                self.lg.debug("Killed by killer.")
                return

            # check the MC configs
            fake_mc = True
            mc_address = None
            mux_address = None
            mc_enabled = False
            mux_enabled = False
            fake_mux = True
            mc_expected_muxes = [""]
            std_expected_muxes = [""]
            if "mc" in config:
                # check if we'll be virtualizing the MC
                if "virtual" in config["mc"]:
                    fake_mc = config["mc"]["virtual"] == True
                # get the MC's address
                if "address" in config["mc"]:
                    mc_address = config["mc"]["address"]
                # check if the MC is enabled
                if "enabled" in config["mc"]:
                    mc_enabled = config["mc"]["enabled"] == True

            if "mux" in config:
                # check what muxes we expect
                if "expected_muxes" in config["mux"]:
                    mc_expected_muxes = config["mux"]["expected_muxes"]
                    std_expected_muxes = config["mux"]["expected_muxes"]  # TODO: clean this up
                if "virtual" in config["mux"]:
                    fake_mux = config["mux"]["virtual"] == True
                if "enabled" in config["mux"]:
                    mux_enabled = config["mux"]["enabled"] == True
                if "address" in config["mux"]:
                    mux_address_split = config["mux"]["address"].split("://")
                    if config["mux"] == "std://mc":  # TODO: this probably needs to look at "address." does nothing atm. snaith mux is likely broken
                        mux_address = config["mux"]["address"]
                        mux_enabled = False  # is is an MC mux, not a standalone one
                    elif mux_address_split[0] == "canmux":
                        mux_address = mux_address_split[1]

            if fake_mux:
                ThisMux = virt.FakeMux
            else:
                ThisMux = Mux481can
            ThisMux.enabled = mux_enabled

            if fake_mc:
                ThisMC = virt.FakeMC
            else:
                ThisMC = MC

            # check the motion controller configs
            fake_mo = True
            mo_address = None
            mo_enabled = False
            if "motion" in config:
                # check if we'll be virtualizing the motion controller
                if "virtual" in config["motion"]:
                    fake_mo = config["motion"]["virtual"] == True
                # get the motion controller's address
                if "uri" in config["motion"]:
                    mo_address = config["motion"]["uri"]
                # check if the motion controlller is enabled
                if "enabled" in config["motion"]:
                    if config["motion"]["enabled"] == True:
                        mo_enabled = True
                        # check args for override of stage enable
                        if ("enable_stage" in args) and (args["enable_stage"] == False):
                            mo_enabled = False

            mc_args = {}
            mc_args["timeout"] = 5
            mc_args["address"] = mc_address
            mc_args["expected_muxes"] = mc_expected_muxes
            mc_args["enabled"] = mc_enabled

            mux_args = {}
            mux_args["address"] = mux_address
            mux_args["expected_muxes"] = std_expected_muxes
            # handle a remapped mux
            if "remap" in config["mux"]:
                remap = {}
                for line in config["mux"]["remap"]:
                    remap[f"{(line[0], line[1])}"] = (line[2], line[3])
            else:
                remap = None
            mux_args["remap"] = remap

            smucfgs = config["smus"]  # the smu configs
            for smucfg in smucfgs:
                smucfg["print_sweep_deets"] = args["print_sweep_deets"]  # apply sweep details setting
            sscfg = config["solarsim"]  # the solar sim config
            sscfg["active_recipe"] = args["light_recipe"]  # throw in recipe
            sscfg["intensity"] = args["light_recipe_int"]  # throw in configured intensity

            # figure out what the sweeps will be like
            sweeps = []
            if args["sweep_check"] == True:
                if args["lit_sweep"] == 0:  # "Dark then Light"
                    sweeps.append({"light_on": False, "first_direction": True})
                    sweeps.append({"light_on": True, "first_direction": True})
                    # sweeps = ["dark", "light"]
                elif args["lit_sweep"] == 1:  # "Light then Dark"
                    sweeps.append({"light_on": True, "first_direction": True})
                    sweeps.append({"light_on": False, "first_direction": True})
                    # sweeps = ["light", "dark"]
                elif args["lit_sweep"] == 2:  # "Only dark"
                    sweeps.append({"light_on": False, "first_direction": True})
                    # sweeps = ["dark"]
                elif args["lit_sweep"] == 3:  # "Only light"
                    sweeps.append({"light_on": True, "first_direction": True})
                    # sweeps = ["light"]

                # insert the reverse ones if we're doing that
                for i in range(len(sweeps)):
                    if args["return_switch"]:
                        rev_sweep = sweeps[i * 2].copy()
                        rev_sweep["first_direction"] = False
                        sweeps.insert(i * 2 + 1, rev_sweep)

            start_q = run_queue.copy()  # make a copy that we might use later in case we're gonna loop forever
            if args["cycles"] != 0:
                run_queue *= int(args["cycles"])  # duplicate the pixel_queue "cycles" times
                p_total = len(run_queue)
            else:  # cycle forever
                p_total = float("inf")

            with contextlib.ExitStack() as stack:  # big context manager to manage equipemnt connections
                # register the equipment comms & db comms instances with the ExitStack for magic cleanup/disconnect

                # user registration TODO: consider moving this kind of thing to the frontend
                uid = db.xadd("users", fields={"str": args["user_name"]}, maxlen=100, approximate=True).decode()

                mux = stack.enter_context(ThisMux(**mux_args))  # init and connect mux
                mux.enabled = mux_enabled
                if mux.enabled:
                    mux.connect()
                mc = stack.enter_context(ThisMC(**mc_args))  # init and connect pcb
                mc.mux = mux  # TODO: remove this hack
                smus = [stack.enter_context(smu_fac(smucfg)(**smucfg)) for smucfg in smucfgs]  # init and connect to smus

                suid = db.xadd("setups", fields={"json": json.dumps(config["setup"])}, maxlen=100, approximate=True).decode()

                # glather the list of device dicts
                device_dicts = [dd for group in run_queue for dd in group]
                layouts = config["substrates"]["layouts"]

                # register substrates, devices, layouts, layout devices, smus, setup slots
                # to get a lookup construct
                lu = dbl.registerer(device_dicts, suid, smus, layouts)
                assert len(lu["device_ids"]) == len(set(lu["device_ids"])), "Every device in a run must be unique."

                # only do contact checking stuff if at least one smu has it enabled
                rs = None
                if not all([smu.cc_mode.upper()=="NONE" for smu in smus]):
                    # check connectivity
                    self.lg.log(29, f"Checking device connectivity...")
                    tosort = []
                    for group in run_queue:
                        for device_dict in group:
                            tosort.append((device_dict["slot"], device_dict["pad"], device_dict["smui"]))
                    tosort.sort(key=lambda x: x[0])  # reorder this for optimal contact checking
                    slots = [x[0] for x in tosort]
                    pads = [x[1] for x in tosort]
                    smuis = [x[2] for x in tosort]

                    # do the contact check
                    rs = Fabric.get_pad_rs(mc, smus, pads, slots, smuis, remap=remap)
                    self.lg.debug(repr(rs))  # log contact check results

                    # send results to db
                    for r in rs:
                        to_upsert = {}
                        to_upsert["substrate_id"] = lu["substrate_ids"][lu["slots"].index(r["slot"])]
                        to_upsert["setup_slot_id"] = lu["slot_ids"][lu["slots"].index(r["slot"])]
                        to_upsert["pad_name"] = str(r["pad"])
                        to_upsert["pass"] = r["data"][0]
                        to_upsert["r"] = r["data"][1]
                        r["ccid"] = db.xadd("tbl_contact_checks", fields={"json": json.dumps(to_upsert)}, maxlen=10000, approximate=True).decode()

                    # notify user of contact check failures
                    fails = [line for line in rs if not line["data"][0]]
                    if any(fails):
                        headline = f'⚠️Found {len(fails)} connection fault(s) in slot(s): {",".join(set([x["slot"] for x in fails]))}'
                        if config["UI"]["bad_connections"] == "ignore":  # ignore mode
                            self.lg.debug(headline)
                        else:
                            self.lg.warning(headline)
                        if config["UI"]["bad_connections"] == "abort":  # abort mode
                            self.lg.warning("Aborting run because of connection failures!")
                            return
                        body = ["Ignoring poor connections can result in the collection of misleading data."]
                        if config["UI"]["bad_connections"] == "ignore":  # ignore mode
                            self.lg.debug("Data from poorly connected devices in this run will be flagged as untrustworthy.")
                            self.lg.debug("Continuting anyway...")
                        else:  # "ask" mode: generate warning dialog for user to decide
                            body.append("Pads with connection faults:")
                            n_cols = 5
                            i = 0
                            row = []
                            for line in fails:
                                i = i + 1
                                if i > n_cols:
                                    body.append(" ".join(row))
                                    i = 0
                                    row = []
                                else:
                                    pair = f'{line["slot"]}-{line["pad"]}'
                                    row.append(f"{pair : <10}")
                            if row:
                                body.append(" ".join(row))

                            payload = {"warn_dialog": {"headline": headline, "body": "\n".join(body), "buttons": ("Ignore and Continue", "Abort the Run")}}
                            self.outq.put({"topic": "status", "payload": json.dumps(payload), "qos": 2})
                            self.lg.log(29, "Waiting for user input on what to do...")
                            self.bc_response.wait()
                            self.bc_response.clear()
                            if self.pkiller.is_set():
                                self.lg.debug("Killed by killer.")
                                return
                            self.lg.warning("Data from poorly connected devices in this run will be flagged as untrustworthy.")
                            self.lg.warning("Continuting anyway...")
                    else:
                        self.lg.log(29, "🟢 All good!")

                ss = stack.enter_context(ill_fac(sscfg)(**sscfg))  # init and connect to solar sim

                # setup motion object
                if fake_mo:
                    mo = Motion(mo_address, pcb_object=virt.FakeMC(), enabled=mo_enabled, fake=fake_mo)
                else:
                    mo = Motion(mo_address, pcb_object=mc, enabled=mo_enabled, fake=fake_mo)
                assert mo.connect() == 0, f"{mo.connect() == 0=}"  # make connection to motion system

                # register a new run
                # rid = db.register_run(uid, conf_a_id, conf_b_id, importlib.metadata.version("centralcontrol"), name=args["run_name_prefix"])
                db.xadd("started_runs", fields={"run": rid}, maxlen=100, approximate=True).decode()

                # register what's in what slot for this run
                for substrate_id in set(lu["substrate_ids"]):
                    slot_id = lu["slot_ids"][lu["substrate_ids"].index(substrate_id)]
                    db.xadd("tbl_slot_substrate_run_mappings", fields={"json": json.dumps({"run_id": rid, "slot_id": slot_id, "substrate_id": substrate_id})}, maxlen=10000, approximate=True).decode()

                # register the devices selected for measurement in this run
                run_devices = [(rid, did) for did in lu["device_ids"]]
                dbl.multiput("tbl_run_devices", run_devices, ["run_id", "device_id"])

                # now go back and attach this run id to the contact check results that go with it
                if rs:
                    ccids = [r["ccid"] for r in rs]
                    db.xadd("rid_to_ccid", fields={rid: json.dumps(ccids)}, maxlen=10000, approximate=True).decode()

                for sm in smus:
                    sm.killer = self.pkiller  # register the kill signal with the smu object
                mppts = [MPPT(sm) for sm in smus]  # spin up all the max power point trackers

                # datalogging setup
                if "datalogger" in config:
                    dler = stack.enter_context(DataLogger(**config["datalogger"]))
                else:
                    dler = None

                # ====== the hardware and configuration is all set up now so the actual run logic begins here ======

                # here's a context manager that ensures the hardware is in the right state at the start and end
                with Fabric.measurement_context(mc, ss, smus, self.outq, db, rid):
                    if self.pkiller.is_set():
                        self.lg.debug("Killed by killer.")
                        return

                    # make sure we have a record of spectral data
                    # TODO: only do this if this run will actually use the light
                    if ss.idn != "disabled":
                        if hasattr(ss, "get_spectrum"):
                            datas = Fabric.record_spectrum(ss, self.outq, self.lg)
                            for ldata in datas:
                                self.log_light_cal(ldata, suid, dbl, args["light_recipe"], rid)
                        else:
                            light_temps = ss.get_temperatures()
                            self.lg.debug(f"Light temperatures: {light_temps}")
                            if any([t > 60 for t in light_temps]):
                                self.lg.error(f"The light is too hot. Temperatures: {light_temps}")
                                return
                            if any([t < 0 for t in light_temps]):
                                self.lg.warning(f"The light is too cold (not warmed up yet). Temperatures: {light_temps}")

                    # set NPLC
                    if args["nplc"] != -1:
                        [sm.setNPLC(args["nplc"]) for sm in smus]

                    remaining = p_total  # number of steps in the routine that still need to be done
                    n_done = 0  # number of steps in the routine that we've completed so far
                    t0 = time.time()  # run start time snapshot

                    if run_queue:
                        n_parallel = len(run_queue[0])

                        if dler is not None:
                            # add one thread for the datalogger
                            n_parallel = n_parallel + 1

                        # we'll use this pool to run several measurement routines in parallel (parallelism set by how much hardware we have)
                        with concurrent.futures.ThreadPoolExecutor(max_workers=n_parallel, thread_name_prefix="device") as executor:
                            # start up the datalogger thread if it hasn't already been started
                            if dler is not None:
                                dh = DataHandler(pixel={}, outq=self.outq)
                                dh.kind = "ai"
                                dl_future = executor.submit(self.datalogger_routine, dler, dh)
                                dl_future.add_done_callback(self.on_routine_done)
                            else:
                                dl_future = None

                            # main run device measurement loop
                            while (remaining > 0) and (not self.pkiller.is_set()):
                                group = run_queue.pop(0)  # pop off the queue item that we'll be working on in this loop

                                dt = time.time() - t0  # seconds since run start
                                if (n_done > 0) and (args["cycles"] != 0):
                                    tpp = dt / n_done  # average time per step
                                    finishtime = time.time() + tpp * remaining
                                    finish_str = datetime.datetime.fromtimestamp(finishtime).strftime("%I:%M%p")
                                    human_str = humanize.naturaltime(datetime.datetime.fromtimestamp(finishtime))
                                    fraction = n_done / p_total
                                    text = f"[{n_done+1}/{p_total}] finishing at {finish_str}, {human_str}"
                                    self.lg.debug(f'{text} for {args["run_name_prefix"]} by {args["user_name"]}')
                                    progress_msg = {"text": text, "fraction": fraction}
                                    self.outq.put({"topic": "progress", "payload": json.dumps(progress_msg), "qos": 2})

                                group_size = len(group)  # how many pixels this group holds
                                dev_labels = [device_dict["device_label"] for device_dict in group]
                                dev_labp = [f"[{l}]" for l in dev_labels]
                                print_label = f'{", ".join(dev_labp)}'
                                theres = np.array([device_dict["pos"] for device_dict in group])
                                self.outq.put({"topic": "plotter/live_devices", "payload": json.dumps(dev_labels), "qos": 2, "retain": True})
                                if group_size > 1:
                                    there = tuple(theres.mean(0))  # the average location of the group
                                else:
                                    there = theres[0]

                                # send a progress message for the frontend's log window
                                self.lg.log(29, f"Step {n_done+1}/{p_total} → {print_label}")

                                # set up light source voting/synchronization (if any)
                                ss.n_sync = group_size

                                # move stage
                                if there and (float("nan") not in there):
                                    # force light off for motion if configured
                                    if "off_during_motion" in config["solarsim"]:
                                        if config["solarsim"]["off_during_motion"] is True:
                                            ss.apply_intensity(0)
                                    mo.goto(there)  # command the stage

                                # select pixel(s)
                                pix_selections = [device_dict["mux_sel"] for device_dict in group]
                                pix_deselections = [(slot, 0) for slot, pad in pix_selections]
                                Fabric.select_pixel(mc, mux_sels=pix_selections)

                                # reset futures list for new round of parallel measurements
                                futures: list[concurrent.futures.Future] = []

                                for device_dict in group:
                                    this_smu = smus[device_dict["smui"]]
                                    this_mppt = mppts[device_dict["smui"]]

                                    # setup data handler for this device
                                    dh = DataHandler(pixel=device_dict, outq=self.outq)

                                    # set virtual smu scaling (just so it knows how much current to produce)
                                    if isinstance(this_smu, virt.FakeSMU):
                                        this_smu.area = device_dict["area"]
                                        this_smu.dark_area = device_dict["dark_area"]

                                    # submit device routines for processing
                                    futures.append(executor.submit(self.device_routine, rid, ss, this_smu, this_mppt, dh, args, config, sweeps, device_dict, suid))
                                    futures[-1].add_done_callback(self.on_routine_done)

                                # wait for the device routine futures to come back
                                max_future_time = None  # TODO: try to calculate an upper limit for this
                                (done, not_done) = concurrent.futures.wait(futures, timeout=max_future_time)  # here is where we wait for one step in the run to complete

                                for futrue in not_done:
                                    self.lg.warning(f"{repr(futrue)} didn't finish in time!")
                                    if not futrue.cancel():
                                        self.lg.warning("and we couldn't cancel it.")

                                # deselect what we had just selected
                                Fabric.select_pixel(mc, mux_sels=pix_deselections)

                                # turn off the SMUs
                                for sm in smus:
                                    sm.outOn(False)

                                n_done += 1
                                remaining = len(run_queue)

                                if (remaining == 0) and (args["cycles"] == 0):
                                    # refresh the deque to loop forever
                                    run_queue = start_q.copy()
                                    remaining = len(run_queue)

                            # use pkiller to ask the datalogger to stop if we're datalogging
                            if isinstance(dl_future, concurrent.futures.Future):
                                self.pkiller.set()
                                concurrent.futures.wait((dl_future,), timeout=10)

    def datalogger_routine(self, dler:DataLogger, dh:DataHandler):
        """runs the data logging tasks"""
        self.lg.debug("Starting the Datalogger routine")
        s = sched.scheduler(time.time, time.sleep)

        class Rescheduler(typing.TypedDict):
            sc: sched.scheduler
            action: typing.Callable
            delay: float

        def dlrunner(dler:DataLogger, ai:DataLogger.AnalogInput, t0:float, dh:DataHandler, rs:None|Rescheduler=None):
            """runs a data logging event then reschedules it"""
            dt = time.time() - t0
            val = dler.read_chan(ai)  # make reading
            self.lg.debug(f"CH{ai['num']} ({ai['name']}): {val} {ai['unit']} @ {dt=}s")
            if val is not None:
                dh.handle_logger_data(ai["num"], dt, ai["name"], val, ai["unit"])

            if rs is not None:
                # reschedule this
                rs["sc"].enter(rs["delay"], 1, rs["action"], argument=(dler, ai, t0, dh, rs))

        t0 = time.time()

        for ai_chan in dler.analog_inputs:
            if ai_chan["enabled"]:
                # do an initial reading now
                dlrunner(dler, ai_chan, t0, dh)

                # schedule the next reading
                s.enter(ai_chan["delay"], 1, dlrunner, argument=(dler, ai_chan, t0, dh, {"sc":s, "action": dlrunner, "delay":ai_chan["delay"]}))

        # loop the scheduler while watching for a pkiller signal to end things
        finished = self.pkiller.is_set()
        while not finished:
            deadline = s.run(blocking=False)
            finished = self.pkiller.wait(deadline)

        self.lg.debug("Datalogger routine has finished")

    def on_routine_done(self, future:concurrent.futures.Future):
        """callback function for when a routine future completes"""
        future_exception = future.exception()  # check if the process died because of an exception
        if future_exception:
            self.lg.error(f"Future failed: {repr(future_exception)}")
            # log the exception's whole call stack for debugging
            tb = traceback.TracebackException.from_exception(future_exception)
            self.lg.debug("".join(tb.format()))

    def device_routine(self, rid: int, ss: LightAPI, sm: SourcemeterAPI, mppt: MPPT, dh: DataHandler, args: dict, config: dict, sweeps: list, pix: dict, suid: int):
        """
        parallelizable. this contains the logic for what a single device experiences during the measurement routine.
        several of these can get scheduled to run concurrently if there are enough SMUs for that.
        """
        data = []
        mppt_enabled = (args["mppt_check"]) and (args["mppt_dwell"] > 0)  # will we do mppt here?
        # with SlothDB(db_uri=config["db"]["uri"]) as db:
        with redis.Redis.from_url(self.mem_db_url) as db:
            dbl = DBLink(db)
            ecs = dbl.counter_sequence()  # experiment counter sequence generator to keep track of the order in which things were done here
            # "Voc" if
            if (args["i_dwell"] > 0) and args["i_dwell_check"]:
                if self.pkiller.is_set():
                    self.lg.debug("Killed by killer.")
                    return []

                ss_args = {}
                ss_args["sourceVoltage"] = False
                ss_args["compliance"] = sm.voltage_limit
                ss_args["setPoint"] = args["i_dwell_value"]
                # NOTE: "a" (auto range) can possibly cause unknown delays between points
                # but that's okay here because timing between points isn't
                # super important with steady state measurements
                ss_args["senseRange"] = "a"
                sm.setupDC(**ss_args)  # type: ignore # initialize the SMU hardware for a steady state measurement

                svoc_steps = int(abs(args["suns_voc"]))  # number of suns-Voc steps we might take
                svoc_step_threshold = 2  # must request at least this many steps before the measurement turns on
                if svoc_steps > svoc_step_threshold:  # suns-Voc is enabled (either up or down)
                    int_min = 10  # minimum settable intensity
                    int_max = args["light_recipe_int"]  # the highest intensity we'll go to
                    int_rng = int_max - int_min  # the magnitude of the range we'll sweep over
                    int_step_size = int_rng / (svoc_steps - 2)  # how big the intensity steps will be
                    intensities = [0, 10]  # values for the first two intensity steps
                    intensities += [round(int_min + (x + 1) * int_step_size) for x in range(svoc_steps - 2)]  # values for the rest of the intensity steps
                else:
                    intensities = []

                if args["suns_voc"] < -svoc_step_threshold:  # suns-voc is up
                    self.lg.debug(f"Doing upwards suns-Voc for {args['i_dwell']} seconds.")
                    dh.kind = "vt_measurement"
                    self.clear_plot("vt_measurement")

                    # db prep
                    isweep_event = {}
                    isweep_event["run_id"] = rid
                    isweep_event["ecs"] = next(ecs)
                    isweep_event["device_id"] = pix["did"]
                    isweep_event["fixed"] = en.Fixed.CURRENT
                    isweep_event["setpoint"] = args["i_dwell_value"]
                    isweep_event["isetpoints"] = intensities
                    isweep_event["effective_area"] = pix["area"]
                    isweepeid = db.xadd("tbl_event:isweep", fields={"json": json.dumps(isweep_event)}, maxlen=1000, approximate=True).decode()
                    # data collection prep
                    datcb = lambda x: (dbl.putsmdat(x, cast(int, isweepeid), en.Event.LIGHT_SWEEP, rid), dh.handle_data(x, False))
                    # do the experiment
                    svtb = self.suns_voc(args["i_dwell"], ss, sm, intensities, datcb)
                    # mark it as done
                    db.xadd("tbl_event:isweeps_done", fields={"id": isweepeid}, maxlen=1000, approximate=True).decode()
                    # keep the data
                    data += svtb

                ss.lit = True  # Voc needs light
                self.lg.debug(f"Measuring voltage at constant current for {args['i_dwell']} seconds.")
                dh.kind = "vt_measurement"
                self.clear_plot("vt_measurement")

                # db prep
                ss_event = {}
                ss_event["run_id"] = rid
                ss_event["ecs"] = next(ecs)
                ss_event["device_id"] = pix["did"]
                ss_event["fixed"] = en.Fixed.CURRENT
                ss_event["setpoint"] = args["i_dwell_value"]
                ss_event["effective_area"] = pix["area"]
                sseid = db.xadd("tbl_event:ss", fields={"json": json.dumps(ss_event)}, maxlen=1000, approximate=True).decode()
                # data collection prep
                datcb = lambda x: (dbl.putsmdat(x, cast(int, sseid), en.Event.SS, rid), dh.handle_data(x, dodb=False))
                # do the experiment
                vt = sm.measure_until(t_dwell=args["i_dwell"], cb=datcb)
                # mark it as done
                db.xadd("tbl_event:ss_done", fields={"id": sseid}, maxlen=1000, approximate=True).decode()
                # keep the data
                data += vt

                # if this was at Voc, use the last measurement as estimate of Voc
                if (args["i_dwell_value"] == 0) and (len(vt) > 1):
                    ssvoc = vt[-1][0]
                else:
                    ssvoc = None

                if args["suns_voc"] > svoc_step_threshold:  # suns-voc is up
                    self.lg.debug(f"Doing downwards suns-Voc for {args['i_dwell']} seconds.")
                    dh.kind = "vt_measurement"
                    self.clear_plot("vt_measurement")
                    intensities_reversed = intensities[::-1]

                    # db prep
                    isweep_event = {}
                    isweep_event["run_id"] = rid
                    isweep_event["ecs"] = next(ecs)
                    isweep_event["device_id"] = pix["did"]
                    isweep_event["fixed"] = en.Fixed.CURRENT
                    isweep_event["setpoint"] = args["i_dwell_value"]
                    isweep_event["isetpoints"] = intensities_reversed
                    isweep_event["effective_area"] = pix["area"]
                    isweepeid = db.xadd("tbl_event:isweep", fields={"json": json.dumps(isweep_event)}, maxlen=1000, approximate=True).decode()
                    # data collection prep
                    datcb = lambda x: (dbl.putsmdat(x, cast(int, isweepeid), en.Event.LIGHT_SWEEP, rid), dh.handle_data(x, dodb=False))
                    # do the experiment
                    svtb = self.suns_voc(args["i_dwell"], ss, sm, intensities_reversed, datcb)
                    # mark it as done
                    db.xadd("tbl_event:isweeps_done", fields={"id": isweepeid}, maxlen=1000, approximate=True).decode()
                    # keep the data
                    data += svtb
            else:
                ssvoc = None

            # perform sweeps
            for sweep in sweeps:
                if sweep["first_direction"]:
                    start_setpoint = args["sweep_start"]
                    end_setpoint = args["sweep_end"]
                    sweep_index = 1
                else:
                    start_setpoint = args["sweep_end"]
                    end_setpoint = args["sweep_start"]
                    sweep_index = 2

                if self.pkiller.is_set():
                    self.lg.debug("Killed by killer.")
                    return data
                self.lg.debug(f"Performing {sweep} sweep (from {start_setpoint}V to {end_setpoint}V)")
                # sweeps may or may not need light
                if sweep["light_on"]:
                    ss.lit = True
                    if sweep["first_direction"]:
                        self.clear_plot("iv_measurement")
                    if isinstance(sm, virt.FakeSMU):
                        sm.intensity = ss.intensity / 100  # tell the simulated device how much light it's getting
                    compliance_area = pix["area"]
                else:
                    ss.lit = False
                    if sweep["first_direction"]:
                        self.clear_plot("iv_measurement")
                    if isinstance(sm, virt.FakeSMU):
                        sm.intensity = 0  # tell the simulated device how much light it's getting
                    compliance_area = pix["dark_area"]

                dh.kind = f"iv_measurement/{sweep_index}"  # TODO: check if this /1 is still needed
                dh.illuminated_sweep = sweep["light_on"]

                sweep_args = {}
                sweep_args["sourceVoltage"] = True
                sweep_args["senseRange"] = "f"
                sweep_args["compliance"] = min((sm.current_limit, Fabric.find_i_limit(area=compliance_area, jmax=args["jmax"], imax=args["imax"])))
                sweep_args["nPoints"] = int(args["iv_steps"])
                sweep_args["stepDelay"] = args["source_delay"] / 1000
                sweep_args["start"] = start_setpoint
                sweep_args["end"] = end_setpoint
                sm.setupSweep(**sweep_args)

                # db prep
                sweep_event = {}
                sweep_event["run_id"] = rid
                sweep_event["ecs"] = next(ecs)
                sweep_event["device_id"] = pix["did"]
                sweep_event["fixed"] = en.Fixed.VOLTAGE
                sweep_event["linear"] = True
                sweep_event["n_points"] = sweep_args["nPoints"]
                sweep_event["from_setpoint"] = sweep_args["start"]
                sweep_event["to_setpoint"] = sweep_args["end"]
                sweep_event["light"] = sweep["light_on"]
                sweep_event["effective_area"] = compliance_area
                sweepeid = db.xadd("tbl_event:sweep", fields={"json": json.dumps(sweep_event)}, maxlen=1000, approximate=True).decode()
                # do the experiment
                iv = sm.measure(sweep_args["nPoints"])
                # record the data
                dbl.putsmdat(iv, sweepeid, en.Event.ELECTRIC_SWEEP, rid)  # type: ignore
                # mark the event's data collection as done
                db.xadd("tbl_event:sweeps_done", fields={"id": sweepeid}, maxlen=1000, approximate=True).decode()
                # do legacy data handling

                dh.handle_data(iv, dodb=False)  # type: ignore
                # keep the data
                data += iv

                if mppt_enabled:
                    # register this curve with the mppt
                    mppt.register_curve(iv, light=sweep["light_on"])

            # TODO: read and interpret parameters for smart mode
            dh.illuminated_sweep = None  # not a sweep

            # mppt if
            if mppt_enabled:
                if self.pkiller.is_set():
                    self.lg.debug("Killed by killer.")
                    return data
                self.lg.debug(f"Performing max. power tracking for {args['mppt_dwell']} seconds.")

                # mppt always needs light
                ss.lit = True
                if isinstance(sm, virt.FakeSMU):
                    # tell the simulated device how much light it's getting
                    sm.intensity = ss.intensity / 100
                compliance_area = pix["area"]

                dh.kind = "mppt_measurement"
                self.clear_plot("mppt_measurement")

                if ssvoc is not None:
                    # tell the mppt what our measured steady state Voc was
                    mppt.Voc = ssvoc

                mppt_args = {}
                mppt_args["duration"] = args["mppt_dwell"]
                mppt_args["NPLC"] = args["nplc"]
                mppt_args["extra"] = args["mppt_params"]
                mppt_args["voc_compliance"] = sm.voltage_limit
                mppt_args["i_limit"] = min((sm.current_limit, Fabric.find_i_limit(area=compliance_area, jmax=args["jmax"], imax=args["imax"])))
                mppt_args["area"] = pix["area"]

                # db prep
                mppt_event = {}
                mppt_event["run_id"] = rid
                mppt_event["ecs"] = next(ecs)
                mppt_event["device_id"] = pix["did"]
                mppt_event["algorithm"] = args["mppt_params"]
                mppt_event["effective_area"] = compliance_area
                mpptid = db.xadd("tbl_event:mppt", fields={"json": json.dumps(mppt_event)}, maxlen=1000, approximate=True).decode()
                # data collection prep
                datcb = lambda x: (dbl.putsmdat(x, cast(int, mpptid), en.Event.MPPT, rid), dh.handle_data(x, dodb=False))
                mppt_args["callback"] = datcb
                # do the experiment
                (mt, vt) = mppt.launch_tracker(**mppt_args)
                # mark the event's data collection as done
                db.xadd("tbl_event:mppt_done", fields={"id": mpptid}, maxlen=1000, approximate=True).decode()

                # TODO: consider moving these into the mpp tracker
                mppt.reset()
                # reset nplc because the mppt can mess with it
                if args["nplc"] != -1:
                    sm.setNPLC(args["nplc"])

                # in the case where we had to do a brief Voc in the mppt because we were running it blind,
                # send that data to the handler
                if len(vt) > 0:
                    dh.kind = "vtmppt_measurement"

                    # db prep
                    ss_event = {}
                    ss_event["run_id"] = rid
                    ss_event["ecs"] = next(ecs)
                    ss_event["device_id"] = pix["did"]
                    ss_event["fixed"] = en.Fixed.CURRENT
                    ss_event["setpoint"] = 0.0
                    ss_event["effective_area"] = compliance_area
                    sseid = db.xadd("tbl_event:ss", fields={"json": json.dumps(ss_event)}, maxlen=1000, approximate=True).decode()
                    # simulate the ssvoc measurement from the voc data returned by the mpp tracker
                    for d in vt:
                        assert len(d) == 4, "Malformed smu data (resistance mode?)"
                        dbl.putsmdat([d], sseid, en.Event.SS, rid)
                        dh.handle_data([d], dodb=False)
                    # mark the event as done
                    db.xadd("tbl_event:ss_done", fields={"id": sseid}, maxlen=1000, approximate=True).decode()
                    # keep the data
                    data += vt

                # keep the mppt data
                data += mt

            # "J_sc" if
            if (args["v_dwell_check"]) and (args["v_dwell"] > 0):
                if self.pkiller.is_set():
                    self.lg.debug("Killed by killer.")
                    return data
                self.lg.debug(f"Measuring current at constant voltage for {args['v_dwell']} seconds.")

                # jsc always needs light
                ss.lit = True
                if isinstance(sm, virt.FakeSMU):
                    # tell the simulated device how much light it's getting
                    sm.intensity = ss.intensity / 100
                compliance_area = pix["area"]

                dh.kind = "it_measurement"
                self.clear_plot("it_measurement")

                ss_args = {}
                ss_args["sourceVoltage"] = True
                ss_args["compliance"] = min((sm.current_limit, Fabric.find_i_limit(area=compliance_area, jmax=args["jmax"], imax=args["imax"])))
                ss_args["setPoint"] = args["v_dwell_value"]
                ss_args["senseRange"] = "a"  # NOTE: "a" can possibly cause unknown delays between points
                sm.setupDC(**ss_args)

                # db prep
                ss_event = {}
                ss_event["run_id"] = rid
                ss_event["ecs"] = next(ecs)
                ss_event["device_id"] = pix["did"]
                ss_event["fixed"] = en.Fixed.VOLTAGE
                ss_event["setpoint"] = args["v_dwell_value"]
                ss_event["effective_area"] = compliance_area
                sseid = db.xadd("tbl_event:ss", fields={"json": json.dumps(ss_event)}, maxlen=1000, approximate=True).decode()
                # data collection prep
                datcb = lambda x: (dbl.putsmdat(x, cast(int, sseid), en.Event.SS, rid), dh.handle_data(x, dodb=False))
                # do the experiment
                it = sm.measure_until(t_dwell=args["v_dwell"], cb=datcb)
                # mark it as done
                db.xadd("tbl_event:ss_done", fields={"id": sseid}, maxlen=1000, approximate=True).decode()
                # keep the data
                data += it

        sm.outOn(False)  # it's probably wise to shut off the smu after every pixel
        return data

    @staticmethod
    def record_spectrum(ss: LightAPI, outq: Queue | mQueue, lg: Logger) -> list[dict]:
        """does spectrum fetching at the start of the standard routine"""
        datas = []
        try:
            # intensity_setpoint = ss.intensity
            intensity_setpoint = ss.active_intensity
            wls, counts = ss.get_spectrum()
            data = [(wl, count) for wl, count in zip(wls, counts)]
            spec = {"data": data, "temps": ss.last_temps, "intensity": (intensity_setpoint,), "idn": ss.idn, "timestamp": datetime.datetime.now().astimezone().isoformat()}
            datas.append(spec)
            spectrum_dict = {"data": data, "intensity": (intensity_setpoint,), "timestamp": time.time()}
            outq.put({"topic": "calibration/spectrum", "payload": json.dumps(spectrum_dict), "qos": 2, "retain": True})
            if intensity_setpoint != 100:
                # now do it again to make sure we have a record of the 100% baseline
                # ss.apply_intensity(100)
                ss.set_intensity(100)  # type: ignore # TODO: this bypasses the API, fix that
                wls, counts = ss.get_spectrum()
                data = [(wl, count) for wl, count in zip(wls, counts)]
                spec = {"data": data, "temps": ss.last_temps, "intensity": (100.0,), "idn": ss.idn, "timestamp": datetime.datetime.now().astimezone().isoformat()}
                datas.append(spec)
                spectrum_dict = {"data": data, "intensity": (100,), "timestamp": time.time()}
                outq.put({"topic": "calibration/spectrum", "payload": json.dumps(spectrum_dict), "qos": 2, "retain": True})
                # ss.apply_intensity(intensity_setpoint)
                ss.set_intensity(100)  # type: ignore # TODO: this bypasses the API, fix that
        except Exception as e:
            lg.debug(f"Failure to collect spectrum data: {repr(e)}")
            # log the exception's whole call stack for debugging
            tb = traceback.TracebackException.from_exception(e)
            lg.debug("".join(tb.format()))
        return datas

    def log_light_cal(
        self,
        data: dict,
        setup_id: int,
        db: DBLink,
        recipe: str | None = None,
        run_id: int | None = None,
    ) -> str:
        """stores away light calibration data"""
        ary = np.array(data["data"])
        area = np.trapz(ary[:, 1], ary[:, 0])
        cal_args = {}
        cal_args["timestamp"] = data["timestamp"]
        cal_args["sid"] = setup_id
        cal_args["rid"] = run_id
        cal_args["temps"] = data["temps"]
        cal_args["raw_spec"] = data["data"]
        cal_args["raw_int_spec"] = area
        cal_args["setpoint"] = data["intensity"]
        cal_args["recipe"] = recipe
        cal_args["idn"] = data["idn"]

        return db.new_light_cal(**cal_args)

    @staticmethod
    def find_i_limit(area: float | None = None, jmax: float | None = None, imax: float | None = None) -> float:
        """Guess what the maximum allowable current should be
        area in cm^2
        jmax in mA/cm^2
        returns value in A (defaults to 0.025A = 0.5cm^2 * 50 mA/cm^2)
        """
        if imax is not None:
            ret_val = imax
        elif (area is not None) and (jmax is not None):
            ret_val = jmax * area / 1000  # scale mA to A
        else:
            # default guess is a 0.5 sqcm device operating at just above the SQ limit for Si
            ret_val = 0.5 * 0.05

        return ret_val

    @staticmethod
    def select_pixel(pcb: MC | virt.FakeMC | Mux481can | virt.FakeMux, mux_sels: list[tuple[str, int]] | list[tuple[str, str]] | None = None):
        """manipulates the mux. returns nothing and throws a value error if there was a filaure"""
        if mux_sels is None:
            mux_sels = [("OFF", 0)]  # empty call disconnects everything

        # ensure we have a list
        if not isinstance(mux_sels, list):
            mux_sels = [mux_sels]

        pcb.set_mux(mux_sels)

    def suns_voc(self, duration: float, light: LightAPI, sm: SourcemeterAPI, intensities: typing.List[int], cb):
        """do a suns-Voc measurement"""
        if isinstance(sm, virt.FakeSMU):
            old_intensity = sm.intensity
        else:
            old_intensity = 1.0
        step_time = duration / len(intensities)
        svt = []
        for intensity_setpoint in intensities:
            light.intensity = intensity_setpoint
            if isinstance(sm, virt.FakeSMU):
                sm.intensity = intensity_setpoint / 100  # tell the simulated device how much light it's getting
            svt += sm.measure_until(t_dwell=step_time, cb=cb)
        if isinstance(sm, virt.FakeSMU):
            sm.intensity = old_intensity  # reset the simulated device's intensity
        return svt

    def clear_plot(self, kind: str):
        """send a message asking a plot to clear its data"""
        self.outq.put({"topic": f"plotter/{kind}/clear", "payload": json.dumps(""), "qos": 2})

    def get_things_to_measure(self, request: dict) -> list[list[dict]]:
        """tabulate a list of items to loop through during the measurement"""
        # int("checkerberrycheddarchew")  # force crash for testing

        config = request["config"]
        args = request["args"]

        center = config["motion"]["centers"]["solarsim"]
        run_q = []  # collections.deque()  # TODO: check if this could just be a list

        if "slots" in request:  # legacy
            # int("checkerberrycheddarchew")  # force crash for testing
            required_cols = ["slot", "user_label", "layout", "bitmask"]
            validated = []  # holds validated slot data
            # the client sent unvalidated slots data
            # validate it and overwrite the origional slots data
            try:
                listlist: list[list[str]] = request["slots"]
                # NOTE: this validation borrows from that done in runpanel
                col_names = listlist.pop(0)
                variables = col_names.copy()
                for rcol in required_cols:
                    assert rcol in col_names
                    variables.remove(rcol)
                # checkmarks = []  # list of lists of bools that tells us which pixels are selected
                # user_labels = []  # list of user lables for going into the device picker store
                # layouts = []  # list of layouts for going into the device picker store
                # areas = []  # list of layouts for going into the device picker store
                # pads = []  # list of layouts for going into the device picker store
                for i, data in enumerate(listlist):
                    assert len(data) == len(col_names)  # check for missing cells
                    slot = data[col_names.index("slot")]
                    assert slot == list(config["slots"].keys())[i]  # check system label validity
                    layout = data[col_names.index("layout")]
                    assert layout in config["substrates"]["layouts"]  # check layout name validity
                    bm_val = int(data[col_names.index("bitmask")].removeprefix("0x"), 16)
                    # li = self.layouts.index(layout)  # layout index
                    # subs_areas = self.areas[li]  # list of pixel areas for this layout
                    # assert 2 ** len(subs_areas) >= bm_val  # make sure the bitmask hex isn't too big for this layout
                    if bm_val == 0:
                        continue  # nothing's selected so we'll skip this row
                    # subs_pads = self.pads[li]  # list of pixel areas for this layout
                    bitmask = bin(bm_val).removeprefix("0b")
                    # subs_cms = [x == "1" for x in f"{bitmask:{'0'}{'>'}{len(subs_areas)}}"][::-1]  # checkmarks (enabled pixels) for this substrate
                    user_label = data[col_names.index("user_label")]
                    # checkmarks.append(subs_cms)
                    datalist = []
                    datalist.append(slot)
                    datalist.append(user_label)
                    # user_labels.append(user_label)
                    datalist.append(layout)
                    datalist.append(bitmask)
                    # layouts.append(layout)
                    # areas.append(subs_areas)
                    # pads.append(subs_pads)

                    for variable in variables:
                        datalist.append(data[col_names.index(variable)])
                    validated.append([str(i), datalist])

            except Exception as e:
                # raise ValueError(f"Failed processing user-crafted slot table data: {repr(e)}")
                self.lg.error(f"Failed processing user-crafted slot table data: {repr(e)}")
                # log the exception's whole call stack for debugging
                tb = traceback.TracebackException.from_exception(e)
                self.lg.debug("".join(tb.format()))
            else:
                # TODO: use validated slot data to update stuff
                pass

        if "IV_stuff" in args:
            stuff = args["IV_stuff"]  # dict from dataframe
            stuff_name = list(stuff.keys())[0]
            bd = stuff[stuff_name]

            # build pixel/group queue for the run
            if len(request["config"]["smus"]) > 1:  # multismu case
                grouping = request["config"]["slots"]["group_order"]
                for group in grouping:
                    group_list = []
                    for smu_index, device in enumerate(group):
                        sort_slot, sort_pad = device  # the slot, pad to sort on

                        for i, slot in enumerate(bd["slot"]):
                            if (slot, bd["pad"][i]) == (sort_slot, sort_pad):  # we have a match
                                pixel_dict = {}
                                pixel_dict["smui"] = smu_index
                                pixel_dict["layout"] = bd["layout"][i]
                                pixel_dict["slot"] = bd["slot"][i]
                                pixel_dict["device_label"] = bd["device_label"][i]
                                pixel_dict["user_label"] = bd["user_label"][i]
                                pixel_dict["pad"] = bd["pad"][i]
                                por = [float("nan") if x is None else x for x in bd["pixel_offset_raw"][i]]  # convert Nones to NaNs
                                sor = [float("nan") if x is None else x for x in bd["substrate_offset_raw"][i]]  # convert Nones to NaNs
                                pixel_dict["pos"] = [c - s - p for c, s, p in zip(center, sor, por)]
                                pixel_dict["mux_sel"] = (pixel_dict["slot"], pixel_dict["pad"])

                                area = bd["area"][i]
                                # handle custom area/dark area
                                if area == -1:
                                    headings = list(bd.keys())
                                    headings.remove("area")
                                    headings.remove("dark_area")
                                    for heading in headings:
                                        if "area" in heading.lower():
                                            if "dark" not in heading.lower():
                                                try:
                                                    area = float(bd[heading][i])
                                                    self.lg.log(29, f'Using user supplied area = {area} [cm^2] for slot {pixel_dict["slot"]}, pad# {pixel_dict["pad"]}')
                                                except:
                                                    pass

                                dark_area = bd["dark_area"][i]
                                if dark_area == -1:  # handle custom dark area
                                    headings = list(bd.keys())
                                    headings.remove("area")
                                    headings.remove("dark_area")
                                    for heading in headings:
                                        if "area" in heading.lower():
                                            if "dark" in heading.lower():
                                                try:
                                                    dark_area = float(bd[heading][i])
                                                    self.lg.log(29, f'Using user supplied dark area = {dark_area} [cm^2] for slot {pixel_dict["slot"]}, pad# {pixel_dict["pad"]}')
                                                except:
                                                    pass

                                # handle the cases where the user didn't tell us an area
                                if (area == -1) and (dark_area == -1):
                                    area = 1.0
                                    dark_area = 1.0
                                    self.lg.warning(f'Assuming area = {area} [cm^2] for slot {pixel_dict["slot"]}, pad# {pixel_dict["pad"]}')
                                    self.lg.warning(f'Assuming dark area = {dark_area} [cm^2] for slot {pixel_dict["slot"]}, pad# {pixel_dict["pad"]}')
                                elif area == -1:
                                    area = dark_area
                                    self.lg.warning(f'Assuming area = {area} [cm^2] for slot {pixel_dict["slot"]}, pad# {pixel_dict["pad"]}')
                                elif dark_area == -1:
                                    dark_area = area
                                    self.lg.warning(f'Assuming dark area = {dark_area} [cm^2] for slot {pixel_dict["slot"]}, pad# {pixel_dict["pad"]}')

                                pixel_dict["area"] = area
                                pixel_dict["dark_area"] = dark_area

                                group_list.append(pixel_dict)
                    if len(group_list) > 0:
                        run_q.append(group_list)

        # disable turbo (parallel, multi smu) mode by unwrapping the groups
        if ("turbo_mode" in args) and (args["turbo_mode"] == False):
            unwrapped_run_queue = []
            for group in run_q:
                for pixel_dict in group:
                    unwrapped_run_queue.append([pixel_dict])
            run_q = unwrapped_run_queue  # overwrite the run_queue with its unwrapped version

        return run_q

    def estop(self, request):
        """emergency stop of the stage"""
        if request["mc_virt"]:
            ThisMC = virt.FakeMC
        else:
            ThisMC = MC
        with ThisMC(request["mc"]) as mc:
            mc.query("b")  # TODO: consider checking return value
        self.lg.warning("Emergency stop command issued. Re-Homing required before any further movements.")

    @staticmethod
    def rtd_r_to_t(r: float, r0: float = 1000.0, poly=None) -> float:
        """converts RTD resistance to temperature. set r0 to 100 for PT100 and 1000 for PT1000"""
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
