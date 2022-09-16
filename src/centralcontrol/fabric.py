#!/usr/bin/env python3

import collections
import concurrent.futures
import time
import traceback
import hmac
import humanize
import datetime
import typing
import contextlib
import json
import numpy as np
import pandas as pd
import multiprocessing
import threading
import signal
from contextlib import contextmanager
from logging import Logger

from paho.mqtt.client import MQTTMessage

from queue import SimpleQueue as Queue
from multiprocessing.queues import SimpleQueue as mQueue

from slothdb.dbsync import SlothDBSync as SlothDB
from slothdb import enums as en

from centralcontrol.illumination import LightAPI
from centralcontrol.illumination import factory as ill_fac

from centralcontrol.sourcemeter import SourcemeterAPI
from centralcontrol.sourcemeter import factory as smu_fac

from centralcontrol.mqtt import MQTTClient
from centralcontrol.mppt import MPPT

from centralcontrol import virt
from centralcontrol.mc import MC
from centralcontrol.motion import Motion

from centralcontrol.logstuff import get_logger


class DataHandler(object):
    """Handler for measurement data."""

    kind: str = ""
    sweep: str = ""
    dbputter: None | typing.Callable[[list[tuple[float, float, float, int]], None | int], int] = None

    def __init__(self, pixel: dict, outq: mQueue):
        """Construct data handler object.

        Parameters
        ----------
        pixel : dict
            Pixel information.
        """
        self.pixel = pixel
        self.outq = outq

    def handle_data(self, data: list[tuple[float, float, float, int]], dodb: bool = True) -> int:
        """Handle measurement data.

        Parameters
        ----------
        data : array-like
            Measurement data.
        """
        result = 0
        payload = {"data": data, "pixel": self.pixel, "sweep": self.sweep}
        if self.dbputter and dodb:
            result = self.dbputter(data, None)
        self.outq.put({"topic": f"data/raw/{self.kind}", "payload": json.dumps(payload), "qos": 2})
        return result


class Fabric(object):
    """High level experiment control logic"""

    current_limit = 0.1  # always safe default

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

    exitcode = 0

    def __init__(self):
        # self.software_revision = __version__
        # print("Software revision: {:s}".format(self.software_revision))

        self.lg = get_logger(".".join([__name__, type(self).__name__]))  # setup logging

        self.lg.debug("Initialized.")

    def run(self) -> int:
        """runs the measurement server. blocks forever"""
        commcls = None
        comms_args = None
        if self.mqttargs["host"] is not None:
            commcls = MQTTClient
            comms_args = self.mqttargs
            comms_args["parent_outq"] = self.outq
        assert commcls is not None, f"{commcls is not None=}"
        assert comms_args is not None, f"{comms_args is not None=}"
        with commcls(**comms_args) as comms:
            # handle SIGTERM gracefully by asking the main loop to break
            signal.signal(signal.SIGTERM, lambda _, __: comms.inq.put("die"))

            # run the message handler, blocking forever
            self.msg_handler(comms.inq)

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

    def msg_handler(self, inq: Queue[str | MQTTMessage]):
        """handle new messages as they come in from comms, the main program loop lives here"""
        future = None  # represents a long-running task
        # decode_topics = ["measurement/run", "util"]  # messages posted to these channels need their payloads decoded
        with concurrent.futures.ProcessPoolExecutor(max_workers=1) as exicuter:
            try:  # this try/except block is for catching keyboard interrupts and then asking the main loop to break
                while True:  # main program loop
                    try:  # this try/except block lets the main loop keep running through programming errors
                        msg = inq.get()  # mostly execution sits right here waiting to be told what to do
                        if isinstance(msg, str):
                            if msg == "die":
                                break
                        else:
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
                                        future = self.submit_for_execution(exicuter, future, self.do_run, request)
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
        if "stage_virt" in task:
            if task["stage_virt"] == True:
                ThisStageMC = virt.FakeMC
        if "pcb_virt" in task:
            if task["pcb_virt"] == True:
                ThisMC = virt.FakeMC

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
    def get_pad_rs(mc: MC | virt.FakeMC, sms: list[SourcemeterAPI], pads: list[int], slots: list[str], device_grouping: list[list[str]]) -> list[dict]:
        """get a list of resistance values for all the connection pads of a given device list"""
        conns = []  # holds the connection info
        if len(slots) > 0:
            hconns = []
            for slot, pad in zip(slots, pads):  # hi-side lines
                line = {}
                line["slot"] = slot
                if slot == "OFF":
                    pad = "HI"
                    selstr = "s"
                    smi = 0
                else:
                    selstr = f"s{slot}{(1<<(7+pad)):05}"
                    smi = SourcemeterAPI.which_smu(device_grouping, f"{slot}{pad}".lower())
                line["pad"] = pad
                line["selstr"] = selstr
                line["smi"] = smi
                hconns.append(line)

            # lo side stuff
            lconns = []  # holds the connection info
            uslots = list(set(slots))  # unique substrates
            lo_side_mux_strings = [("TOP", f"{(1<<0):05}"), ("BOT", f"{(1<<1):05}")]
            for uslot in uslots:
                for pad, sel in lo_side_mux_strings:
                    line = {}
                    line["slot"] = uslot
                    if uslot == "OFF":
                        line["pad"] = "LO"
                        line["selstr"] = "s"
                        line["smi"] = 0
                        lconns.append(line)
                        break
                    else:
                        line["pad"] = pad
                        line["selstr"] = f"s{uslot}{sel}"
                        line["smi"] = SourcemeterAPI.which_smu(device_grouping, f"{uslot}1".lower())
                        lconns.append(line)

            Fabric.select_pixel(mc)  # ensure we start with devices all deselected
            for sm in sms:
                sm.enable_cc_mode(True)  # ccmode setup leaves us in hi-side checking mode, so we do that first

            last_slot = None
            for line in hconns:
                if last_slot and (last_slot != line["slot"]) and (last_slot != "OFF"):
                    Fabric.select_pixel(mc, [f"s{last_slot}0"])  # make sure the last slot is cleaned up
                Fabric.select_pixel(mc, [line["selstr"]])
                line["data"] = sms[line["smi"]].do_contact_check(False)
            conns += hconns

            for line in lconns:
                if last_slot and (last_slot != line["selstr"]) and (last_slot != "OFF"):
                    Fabric.select_pixel(mc, [f"s{last_slot}0"])  # make sure the last slot is cleaned up
                Fabric.select_pixel(mc, [line["selstr"]])
                line["data"] = sms[line["smi"]].do_contact_check(True)
            conns += lconns
        return conns

    def util_round_robin(self, task: dict, AnMC: type[MC] | type[virt.FakeMC]):
        """handles message from the frontend requesting a round robin-type thing"""
        # inject the no connect case
        task["slots"].insert(0, "OFF")
        task["pads"].insert(0, "OFF")
        task["mux_strings"].insert(0, "s")

        slots = task["slots"]
        pads = task["pads"]
        ms = task["mux_strings"]
        dev_grp = task["device_grouping"]

        if len(slots) > 0:
            with contextlib.ExitStack() as stack:  # handles the proper cleanup of the hardware
                mc = stack.enter_context(AnMC(task["pcb"], timeout=5))
                smus = [stack.enter_context(smu_fac(smucfg)(**smucfg)) for smucfg in task["smu"]]
                Fabric.select_pixel(mc)  # ensure we start with devices all deselected
                if task["type"] == "connectivity":
                    rs = Fabric.get_pad_rs(mc, smus, pads, slots, dev_grp)
                    for line in rs:
                        if not line["data"][0]:
                            name = f'{line["slot"]}-{line["pad"]}'
                            filler = " "
                            self.lg.log(29, f"🔴 The {name:{filler}<6} pad has a connection fault")
                else:
                    for sm in smus:
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
                    for i, slot in enumerate(slots):
                        dev = pads[i]
                        if slot == "none":
                            slot_words = "[Everything disconnected]"
                        else:
                            slot_words = f"[{slot}{dev:n}]"
                        mux_string = ms[i]
                        Fabric.select_pixel(mc, [mux_string])  # select the device
                        if slot == "none":
                            smu_index = 0  # I guess we should just use smu[0] for the all switches open case
                        else:
                            smu_index = SourcemeterAPI.which_smu(dev_grp, f"{slot}{int(dev)}".lower())  # figure out which smu owns the device
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
                                status = int(m[4])  # type: ignore
                                in_compliance = (1 << 3) & status  # check compliance bit (3) in status word
                                if not (in_compliance) and (ohm < 3000) and (ohm > 500):
                                    self.lg.log(29, f"{slot_words} could be a PT1000 RTD at {self.rtd_r_to_t(ohm):.1f} °C")
                        if slot != "none":
                            Fabric.select_pixel(mc, [f"s{slot}0"])  # disconnect the slot
                    for sm in smus:
                        sm.enable_cc_mode(False)
                Fabric.select_pixel(mc)  # disconnect everyone
        self.lg.log(29, "Round robin task complete.")

    def util_check_health(self, task: dict, AnMC: type[MC] | type[virt.FakeMC], AStageMC: type[MC] | type[virt.FakeMC]):
        """handles message from the frontend requesting a utility health check"""
        if "pcb" in task:
            self.lg.log(29, f'Checking MC@{task["pcb"]}...')
            try:
                with AnMC(task["pcb"], timeout=5) as mc:
                    self.lg.debug(f"MC firmware version: {mc.firmware_version}")
                    self.lg.debug(f"MC axes: {mc.detected_axes}")
                    self.lg.debug(f"MC muxes: {mc.detected_muxes}")
                    self.lg.log(29, f"🟢 PASS!")
            except Exception as e:
                self.lg.warning(f"🔴 FAIL: {repr(e)}")
                # log the exception's whole call stack trace for debugging
                tb = traceback.TracebackException.from_exception(e)
                self.lg.debug("".join(tb.format()))
        if "smu" in task:
            smucfgs = task["smu"]  # a list of sourcemeter configurations
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
                    data = ss.get_spectrum()
                    temps = ss.last_temps
                    if not isinstance(data, tuple) or len(data) != 2:  # check data shape
                        data = None
                        emsg.append(f"🔴 Spectrum data was malformed.")
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
        with AnMC(task["pcb"], timeout=5) as mc:
            # special case for pixel selection to avoid parallel connections
            if task["pcb_cmd"].startswith("s") and ("stream" not in task["pcb_cmd"]) and (len(task["pcb_cmd"]) != 1):
                mc.query("s")  # deselect all before selecting one
            result = mc.query(task["pcb_cmd"])
            if result == "":
                self.lg.debug(f"Command acknowledged: {task['pcb_cmd']}")
            else:
                self.lg.warning(f"Command {task['pcb_cmd']} not acknowleged with {result}")

    def util_goto(self, task: dict, AnMC: type[MC] | type[virt.FakeMC]):
        """utility function to send the stage somewhere"""
        with AnMC(task["pcb"], timeout=5) as mc:
            mo = Motion(address=task["stage_uri"], pcb_object=mc)
            assert mo.connect() == 0, f"{mo.connect() == 0=}"  # make connection to motion system
            mo.goto(task["pos"])
            self.send_pos(mo)

    def util_read_stage(self, task: dict, AnMC: type[MC] | type[virt.FakeMC]):
        """utility function to send the stage's position up to the front end"""
        with AnMC(task["pcb"], timeout=5) as mc:
            mo = Motion(address=task["stage_uri"], pcb_object=mc)
            assert mo.connect() == 0, f"{mo.connect() == 0=}"  # make connection to motion system
            self.send_pos(mo)

    def home_stage(self, task: dict, AnMC: type[MC] | type[virt.FakeMC]):
        """homes the stage"""
        with AnMC(task["pcb"], timeout=5) as mc:
            mo = Motion(address=task["stage_uri"], pcb_object=mc)
            assert mo.connect() == 0, f"{mo.connect() == 0=}"  # make connection to motion system
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
            if future.running():
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
        if "rundata" in request:
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
                                try:
                                    i_limits = [x["current_limit"] for x in request["config"]["smu"]]
                                    i_limit = min(i_limits)
                                except:
                                    i_limit = 0.1  # use this default if we can't work out a limit from the configuration
                                self.current_limit = i_limit
                                things_to_measure = self.get_things_to_measure(rundata)
                                self.standard_routine(things_to_measure, rundata)
                                self.lg.log(29, "Run complete!")

    @staticmethod
    @contextmanager
    def measurement_context(mc: MC | virt.FakeMC, ss: LightAPI, smus: list[SourcemeterAPI], outq: Queue | mQueue, db: SlothDB, rid: int):
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

            db.complete_run(rid)  # mark run as complete
            # db.vac()  # mantain db

    def standard_routine(self, run_queue, request: dict) -> None:
        """perform the normal measurement routine on a given list of pixels"""

        # int("checkerberrycheddarchew")  # force crash for testing

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

        # turbo (multiSMU) mode
        turbo_mode = True  # by default use all SMUs available
        if ("turbo_mode" in args) and (args["turbo_mode"] == False):
            turbo_mode = False

        # check the MC configs
        fake_mc = True
        mc_address = None
        mc_enabled = False
        mc_expected_muxes = [""]
        if "controller" in config:
            # check if we'll be virtualizing the MC
            if "virtual" in config["controller"]:
                fake_mc = config["controller"]["virtual"] == True
            # get the MC's address
            if "address" in config["controller"]:
                mc_address = config["controller"]["address"]
            # check if the MC is enabled
            if "enabled" in config["controller"]:
                mc_enabled = config["controller"]["enabled"] == True
            # check what muxes we expect
            if "expected_muxes" in config["controller"]:
                mc_expected_muxes = config["controller"]["expected_muxes"]
        if fake_mc:
            ThisMC = virt.FakeMC
        else:
            ThisMC = MC

        # check the motion controller configs
        fake_mo = True
        mo_address = None
        mo_enabled = False
        if "stage" in config:
            # check if we'll be virtualizing the motion controller
            if "virtual" in config["stage"]:
                fake_mo = config["stage"]["virtual"] == True
            # get the motion controller's address
            if "uri" in config["stage"]:
                mo_address = config["stage"]["uri"]
            # check if the motion controlller is enabled
            if "enabled" in config["stage"]:
                if config["stage"]["enabled"] == True:
                    mo_enabled = True
                    # check args for override of stage enable
                    if ("enable_stage" in args) and (args["enable_stage"] == False):
                        mo_enabled = False

        mc_args = {}
        mc_args["timeout"] = 5
        mc_args["address"] = mc_address
        mc_args["expected_muxes"] = mc_expected_muxes
        mc_args["enabled"] = mc_enabled

        smucfgs = request["config"]["smu"]  # the smu configs
        for smucfg in smucfgs:
            smucfg["print_sweep_deets"] = request["args"]["print_sweep_deets"]  # apply sweep details setting
        sscfg = request["config"]["solarsim"]  # the solar sim config
        sscfg["active_recipe"] = request["args"]["light_recipe"]  # throw in recipe
        sscfg["intensity"] = request["args"]["light_recipe_int"]  # throw in configured intensity

        # figure out what the sweeps will be like
        sweeps = []
        if args["sweep_check"] == True:
            # detmine type of sweeps to perform
            s = args["lit_sweep"]
            if s == 0:
                sweeps = ["dark", "light"]
            elif s == 1:
                sweeps = ["light", "dark"]
            elif s == 2:
                sweeps = ["dark"]
            elif s == 3:
                sweeps = ["light"]

        if turbo_mode == False:  # the user has explicitly asked not to use turbo mode
            # we'll unwrap the run queue here so that we get ungrouped queue itemsrtd
            unwrapped_run_queue = collections.deque()
            for thing in run_queue:
                for key, val in thing.items():
                    unwrapped_run_queue.append({key: val})
            run_queue = unwrapped_run_queue  # overwrite the run_queue with its unwrapped version

        start_q = run_queue.copy()  # make a copy that we might use later in case we're gonna loop forever
        if args["cycles"] != 0:
            run_queue *= int(args["cycles"])  # duplicate the pixel_queue "cycles" times
            p_total = len(run_queue)
        else:
            p_total = float("inf")

        with contextlib.ExitStack() as stack:  # big context manager to manage equipemnt connections
            # register the equipment comms & db comms instances with the ExitStack for magic cleanup/disconnect
            db = stack.enter_context(SlothDB(db_uri=request["config"]["db"]["uri"]))

            # user validation
            user = request["args"]["user_name"]
            uidfetch = db.get(f"{db.schema}.tbl_users", ("id",), f"name = '{user}'")
            # get userid (via new registration if needed)
            if uidfetch == []:  # user does not exist
                uid = db.new_user(user)  # register new
            else:
                # user exists
                uid = uidfetch[0][0]
                active = db.get(f"{db.schema}.tbl_users", ("active",), f"id = {uid}")[0][0]
                if active is False:
                    uid = -1
                    raise ValueError(f"{user} is not a valid user name")

            mc = stack.enter_context(ThisMC(**mc_args))  # init and connect pcb
            smus = [stack.enter_context(smu_fac(smucfg)(**smucfg)) for smucfg in smucfgs]  # init and connect to smus

            # check connectivity
            self.lg.log(29, f"Checking device connectivity...")
            slot_pad = []
            for key, slot in args["IV_stuff"]["system_label"].items():
                slot_pad.append((slot, args["IV_stuff"]["mux_index"][key]))
            slot_pad.sort(key=lambda x: x[0])
            pads = [x[1] for x in slot_pad]
            slots = [x[0] for x in slot_pad]
            rs = Fabric.get_pad_rs(mc, smus, pads, slots, config["substrates"]["device_grouping"])
            fails = [line for line in rs if not line["data"][0]]
            if any(fails):
                headline = f'⚠️Found {len(fails)} connection fault(s) in slot(s): {",".join(set([x["slot"] for x in fails]))}'
                self.lg.warning(headline)
                if config["UI"]["bad_connections"] == "abort":  # abort mode
                    return
                body = ["Ignoring poor connections can result in the collection of misleading data."]
                if config["UI"]["bad_connections"] == "ignore":  # ignore mode
                    pass
                else:  # "ask" mode: generate warning dialog for user to decide
                    body.append("Pads with connection faults:")
                    for line in fails:
                        body.append(f'{line["slot"]}-{line["pad"]}')

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
                mo = Motion(mo_address, pcb_object=virt.FakeMC(), enabled=mo_enabled)
            else:
                mo = Motion(mo_address, pcb_object=mc, enabled=mo_enabled)
            assert mo.connect() == 0, f"{mo.connect() == 0=}"  # make connection to motion system

            rid = db.new_run(uid, site=config["setup"]["site"], setup=config["setup"]["name"], name=args["run_name_prefix"])  # register a new run
            for sm in smus:
                sm.killer = self.pkiller  # register the kill signal with the smu object
            mppts = [MPPT(sm) for sm in smus]  # spin up all the max power point trackers

            # ====== the hardware and configuration is all set up now so the actual run logic begins here ======

            # here's a context manager that ensures the hardware is in the right state at the start and end
            with Fabric.measurement_context(mc, ss, smus, self.outq, db, rid):

                if self.pkiller.is_set():
                    self.lg.debug("Killed by killer.")
                    return

                # make sure we have a record of spectral data
                Fabric.record_spectrum(ss, self.outq, self.lg)

                # set NPLC
                if args["nplc"] != -1:
                    [sm.setNPLC(args["nplc"]) for sm in smus]

                remaining = p_total  # number of steps in the routine that still need to be done
                n_done = 0  # number of steps in the routine that we've completed so far
                t0 = time.time()  # run start time snapshot

                while (remaining > 0) and (not self.pkiller.is_set()):  # main run loop
                    q_item = run_queue.popleft()  # pop off the queue item that we'll be working on in this loop

                    dt = time.time() - t0  # seconds since run start
                    if (n_done > 0) and (args["cycles"] != 0):
                        tpp = dt / n_done  # average time per step
                        finishtime = time.time() + tpp * remaining
                        finish_str = datetime.datetime.fromtimestamp(finishtime).strftime("%I:%M%p")
                        human_str = humanize.naturaltime(datetime.datetime.fromtimestamp(finishtime))
                        fraction = n_done / p_total
                        text = f"[{n_done+1}/{p_total}] finishing at {finish_str}, {human_str}"
                        self.lg.debug(f'{text} for {args["run_name_prefix"]} by {user}')
                        progress_msg = {"text": text, "fraction": fraction}
                        self.outq.put({"topic": "progress", "payload": json.dumps(progress_msg), "qos": 2})

                    n_parallel = len(q_item)  # how many pixels this group holds
                    dev_labels = [val["device_label"] for key, val in q_item.items()]
                    dev_labp = [f"[{l}]" for l in dev_labels]
                    print_label = f'{", ".join(dev_labp)}'
                    theres = np.array([val["pos"] for key, val in q_item.items()])
                    self.outq.put({"topic": "plotter/live_devices", "payload": json.dumps(dev_labels), "qos": 2, "retain": True})
                    if n_parallel > 1:
                        there = tuple(theres.mean(0))  # the average location of the group
                    else:
                        there = theres[0]

                    # send a progress message for the frontend's log window
                    self.lg.log(29, f"Step {n_done+1}/{p_total} → {print_label}")

                    # set up light source voting/synchronization (if any)
                    ss.n_sync = n_parallel

                    # move stage
                    if (there is not None) and (float("inf") not in there) and (float("-inf") not in there):
                        # force light off for motion if configured
                        if "off_during_motion" in config["solarsim"]:
                            if config["solarsim"]["off_during_motion"] is True:
                                ss.apply_intensity(0)
                        mo.goto(there)  # command the stage

                    # select pixel(s)
                    pix_selection_strings = [val["mux_string"] for key, val in q_item.items()]
                    pix_deselection_strings = [f"{x[:-5]}0" for x in pix_selection_strings]
                    Fabric.select_pixel(mc, mux_string=pix_selection_strings)

                    # we'll use this pool to run several measurement routines in parallel (parallelism set by how much hardware we have)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(smus), thread_name_prefix="device") as executor:

                        # keeps track of the parallelized objects
                        futures: list[concurrent.futures.Future] = []

                        for smu_index, pixel in q_item.items():
                            this_smu = smus[smu_index]
                            this_mppt = mppts[smu_index]
                            light_area = pixel["area"]
                            dark_area = pixel["dark_area"]

                            # setup data handler for this device
                            dh = DataHandler(pixel=pixel, outq=self.outq)

                            # get or estimate compliance current values for this device
                            compliance_i = self.compliance_current_guess(area=light_area, jmax=args["jmax"], imax=args["imax"])
                            dark_compliance_i = self.compliance_current_guess(area=dark_area, jmax=args["jmax"], imax=args["imax"])

                            # set virtual smu scaling (just so it knows how much current to produce)
                            if isinstance(this_smu, virt.FakeSMU):
                                this_smu.area = light_area

                            # submit for processing
                            futures.append(executor.submit(self.device_routine, rid, ss, this_smu, this_mppt, dh, compliance_i, dark_compliance_i, args, config, sweeps, light_area, dark_area))
                            futures[-1].add_done_callback(self.on_device_routine_done)

                        # wait for the futures to come back
                        max_future_time = None  # TODO: try to calculate an upper limit for this
                        (done, not_done) = concurrent.futures.wait(futures, timeout=max_future_time)

                        for futrue in not_done:
                            self.lg.warning(f"{repr(futrue)} didn't finish in time!")
                            if not futrue.cancel():
                                self.lg.warning("and we couldn't cancel it.")

                    # deselect what we had just selected
                    Fabric.select_pixel(mc, mux_string=pix_deselection_strings)

                    # turn off the SMUs
                    for sm in smus:
                        sm.outOn(False)

                    n_done += 1
                    remaining = len(run_queue)

                    if (remaining == 0) and (args["cycles"] == 0):
                        # refresh the deque to loop forever
                        run_queue = start_q.copy()
                        remaining = len(run_queue)

    def on_device_routine_done(self, future: concurrent.futures.Future):
        """callback function for when a device routine future completes"""
        future_exception = future.exception()  # check if the process died because of an exception
        if future_exception:
            self.lg.error(f"Future failed: {repr(future_exception)}")
            # log the exception's whole call stack for debugging
            tb = traceback.TracebackException.from_exception(future_exception)
            self.lg.debug("".join(tb.format()))

    def device_routine(self, rid: int, ss: LightAPI, sm: SourcemeterAPI, mppt: MPPT, dh: DataHandler, compliance_i: float, dark_compliance_i: float, args: dict, config: dict, sweeps: list, area: float, dark_area: float):
        """
        parallelizable. this contains the logic for what a single device experiences during the measurement routine.
        several of these can get scheduled to run concurrently if there are enough SMUs for that.
        """
        data = []
        with SlothDB(db_uri=config["db"]["uri"]) as db:
            dh.dbputter = db.putsmdat
            mppt.absolute_current_limit = compliance_i

            # "Voc" if
            if (args["i_dwell"] > 0) and args["i_dwell_check"]:
                if self.pkiller.is_set():
                    self.lg.debug("Killed by killer.")
                    return []

                ss_args = {}
                ss_args["sourceVoltage"] = False
                if ("ccd" in config) and ("max_voltage" in config["ccd"]):
                    ss_args["compliance"] = config["ccd"]["max_voltage"]
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
                    eid = db.new_event(rid, en.Event.LIGHT_SWEEP, sm.address)  # register new light sweep
                    deets = {"label": dh.pixel["device_label"], "slot": f'{dh.pixel["sub_name"]}{dh.pixel["pixel"]}', "area": area}
                    deets["fixed"] = en.Fixed.VOLTAGE
                    deets["setpoint"] = ss_args["setPoint"]
                    deets["isetpoints"] = intensities
                    db.upsert(f"{db.schema}.tbl_isweep_events", deets, eid)  # save event details
                    db.eid = eid  # register event id for datahandler
                    svtb = self.suns_voc(args["i_dwell"], ss, sm, intensities, dh)  # do the experiment
                    db.eid = None  # unregister event id
                    db.complete_event(eid)  # mark light sweep as done
                    data += svtb  # keep the data

                ss.lit = True  # Voc needs light
                self.lg.debug(f"Measuring voltage at constant current for {args['i_dwell']} seconds.")
                dh.kind = "vt_measurement"
                self.clear_plot("vt_measurement")
                # db prep
                eid = db.new_event(rid, en.Event.SS, sm.address)  # register new ss event
                deets = {"label": dh.pixel["device_label"], "slot": f'{dh.pixel["sub_name"]}{dh.pixel["pixel"]}', "area": area}
                deets["fixed"] = en.Fixed.CURRENT
                deets["setpoint"] = ss_args["setPoint"]
                db.upsert(f"{db.schema}.tbl_ss_events", deets, eid)  # save event details
                db.eid = eid  # register event id for datahandler
                vt = sm.measure_until(t_dwell=args["i_dwell"], cb=dh.handle_data)
                db.eid = None  # unregister event id
                db.complete_event(eid)  # mark ss event as done
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
                    eid = db.new_event(rid, en.Event.LIGHT_SWEEP, sm.address)  # register new light sweep
                    deets = {"label": dh.pixel["device_label"], "slot": f'{dh.pixel["sub_name"]}{dh.pixel["pixel"]}', "area": area}
                    deets["fixed"] = en.Fixed.VOLTAGE
                    deets["setpoint"] = ss_args["setPoint"]
                    deets["isetpoints"] = intensities_reversed
                    db.upsert(f"{db.schema}.tbl_isweep_events", deets, eid)  # save event details
                    db.eid = eid  # register event id for datahandler
                    svta = self.suns_voc(args["i_dwell"], ss, sm, intensities_reversed, dh)  # do the experiment
                    db.eid = None  # unregister event id
                    db.complete_event(eid)  # mark light sweep as done
                    data += svta
                    if isinstance(sm, virt.FakeSMU):
                        sm.intensity = 1.0  # reset the simulated device's intensity
            else:
                ssvoc = None

            # perform sweeps
            for sweep in sweeps:
                self.clear_plot("iv_measurement")
                if self.pkiller.is_set():
                    self.lg.debug("Killed by killer.")
                    return data
                self.lg.debug(f"Performing first {sweep} sweep (from {args['sweep_start']}V to {args['sweep_end']}V)")
                # sweeps may or may not need light
                if sweep == "dark":
                    ss.lit = False
                    if isinstance(sm, virt.FakeSMU):
                        sm.intensity = 0  # tell the simulated device how much light it's getting
                    sweep_current_limit = dark_compliance_i
                else:
                    ss.lit = True
                    if isinstance(sm, virt.FakeSMU):
                        sm.intensity = ss.intensity / 100  # tell the simulated device how much light it's getting
                    sweep_current_limit = compliance_i

                dh.kind = "iv_measurement/1"  # TODO: check if this /1 is still needed
                dh.sweep = sweep

                sweep_args = {}
                sweep_args["sourceVoltage"] = True
                sweep_args["senseRange"] = "f"
                sweep_args["compliance"] = sweep_current_limit
                sweep_args["nPoints"] = int(args["iv_steps"])
                sweep_args["stepDelay"] = args["source_delay"] / 1000
                sweep_args["start"] = float(args["sweep_start"])
                sweep_args["end"] = float(args["sweep_end"])
                sm.setupSweep(**sweep_args)

                # db prep
                eid = db.new_event(rid, en.Event.ELECTRIC_SWEEP, sm.address)  # register new ss event
                deets = {"label": dh.pixel["device_label"], "slot": f'{dh.pixel["sub_name"]}{dh.pixel["pixel"]}'}
                deets["fixed"] = en.Fixed.VOLTAGE
                deets["n_points"] = sweep_args["nPoints"]
                deets["from_setpoint"] = sweep_args["start"]
                deets["to_setpoint"] = sweep_args["end"]
                deets["area"] = area
                deets["dark_area"] = dark_area
                if sweep == "dark":
                    deets["light"] = False
                else:
                    deets["light"] = True
                db.upsert(f"{db.schema}.tbl_sweep_events", deets, eid)  # save event details
                iv1 = sm.measure(sweep_args["nPoints"])
                db.putsmdat(iv1, eid)  # type: ignore
                db.complete_event(eid)  # mark event as done
                dh.handle_data(iv1, dodb=False)  # type: ignore
                data += iv1

                # register this curve with the mppt
                mppt.register_curve(iv1, light=(sweep == "light"))

                if args["return_switch"] == True:
                    if self.pkiller.is_set():
                        self.lg.debug("Killed by killer.")
                        return data
                    self.lg.debug(f"Performing second {sweep} sweep (from {args['sweep_end']}V to {args['sweep_start']}V)")

                    dh.kind = "iv_measurement/2"
                    dh.sweep = sweep

                    sweep_args = {}
                    sweep_args["sourceVoltage"] = True
                    sweep_args["senseRange"] = "f"
                    sweep_args["compliance"] = sweep_current_limit
                    sweep_args["nPoints"] = int(args["iv_steps"])
                    sweep_args["stepDelay"] = args["source_delay"] / 1000
                    sweep_args["start"] = args["sweep_end"]
                    sweep_args["end"] = args["sweep_start"]
                    sm.setupSweep(**sweep_args)

                    eid = db.new_event(rid, en.Event.ELECTRIC_SWEEP, sm.address)  # register new ss event
                    deets = {"label": dh.pixel["device_label"], "slot": f'{dh.pixel["sub_name"]}{dh.pixel["pixel"]}'}
                    deets["fixed"] = en.Fixed.VOLTAGE
                    deets["n_points"] = sweep_args["nPoints"]
                    deets["from_setpoint"] = sweep_args["start"]
                    deets["to_setpoint"] = sweep_args["end"]
                    deets["area"] = area
                    deets["dark_area"] = dark_area
                    if sweep == "dark":
                        deets["light"] = False
                    else:
                        deets["light"] = True
                    db.upsert(f"{db.schema}.tbl_sweep_events", deets, eid)  # save event details
                    iv2 = sm.measure(sweep_args["nPoints"])
                    db.putsmdat(iv2, eid)  # type: ignore
                    db.complete_event(eid)  # mark event as done
                    dh.handle_data(iv2, dodb=False)  # type: ignore
                    data += iv2

                    mppt.register_curve(iv2, light=(sweep == "light"))

            # TODO: read and interpret parameters for smart mode
            dh.sweep = ""  # not a sweep

            # mppt if
            if (args["mppt_check"]) and (args["mppt_dwell"] > 0):
                if self.pkiller.is_set():
                    self.lg.debug("Killed by killer.")
                    return data
                self.lg.debug(f"Performing max. power tracking for {args['mppt_dwell']} seconds.")
                # mppt needs light
                ss.lit = True

                dh.kind = "mppt_measurement"
                self.clear_plot("mppt_measurement")

                if ssvoc is not None:
                    # tell the mppt what our measured steady state Voc was
                    mppt.Voc = ssvoc

                mppt_args = {}
                mppt_args["duration"] = args["mppt_dwell"]
                mppt_args["NPLC"] = args["nplc"]
                mppt_args["extra"] = args["mppt_params"]
                mppt_args["callback"] = dh.handle_data
                if ("ccd" in config) and ("max_voltage" in config["ccd"]):
                    mppt_args["voc_compliance"] = config["ccd"]["max_voltage"]
                mppt_args["i_limit"] = compliance_i
                mppt_args["area"] = area

                eid = db.new_event(rid, en.Event.MPPT, sm.address)  # register new mppt event
                deets = {"label": dh.pixel["device_label"], "slot": f'{dh.pixel["sub_name"]}{dh.pixel["pixel"]}', "area": area}
                deets["algorithm"] = args["mppt_params"]
                db.upsert(f"{db.schema}.tbl_mppt_events", deets, eid)  # save event details
                db.eid = eid  # register event id for datahandler
                (mt, vt) = mppt.launch_tracker(**mppt_args)
                db.eid = None  # unregister event id
                db.complete_event(eid)  # mark event as done
                mppt.reset()

                # reset nplc because the mppt can mess with it
                if args["nplc"] != -1:
                    sm.setNPLC(args["nplc"])

                # in the case where we had to do a brief Voc in the mppt because we were running it blind,
                # send that data to the handler
                if len(vt) > 0:
                    dh.kind = "vtmppt_measurement"
                    eid = db.new_event(rid, en.Event.SS, sm.address)  # register new ss event
                    deets = {"label": dh.pixel["device_label"], "slot": f'{dh.pixel["sub_name"]}{dh.pixel["pixel"]}', "area": area}
                    deets["fixed"] = en.Fixed.CURRENT
                    deets["setpoint"] = 0.0
                    db.upsert(f"{db.schema}.tbl_ss_events", deets, eid)  # save event details
                    db.eid = eid  # register event id for datahandler
                    for d in vt:  # simulate the ssvoc measurement from the voc data returned by the mpp tracker
                        dh.handle_data([d])  # type: ignore
                    db.eid = None  # unregister event id
                    db.complete_event(eid)  # mark ss event as done

                data += vt
                data += mt

            # "J_sc" if
            if (args["v_dwell_check"]) and (args["v_dwell"] > 0):
                if self.pkiller.is_set():
                    self.lg.debug("Killed by killer.")
                    return data
                self.lg.debug(f"Measuring current at constant voltage for {args['v_dwell']} seconds.")
                # jsc needs light
                ss.lit = True

                dh.kind = "it_measurement"
                self.clear_plot("it_measurement")

                ss_args = {}
                ss_args["sourceVoltage"] = True
                ss_args["compliance"] = compliance_i
                ss_args["setPoint"] = args["v_dwell_value"]
                ss_args["senseRange"] = "a"  # NOTE: "a" can possibly cause unknown delays between points
                sm.setupDC(**ss_args)

                eid = db.new_event(rid, en.Event.SS, sm.address)  # register new ss event
                deets = {"label": dh.pixel["device_label"], "slot": f'{dh.pixel["sub_name"]}{dh.pixel["pixel"]}', "area": area}
                deets["fixed"] = en.Fixed.VOLTAGE
                deets["setpoint"] = ss_args["setPoint"]
                db.upsert(f"{db.schema}.tbl_ss_events", deets, eid)  # save event details
                db.eid = eid  # register event id for datahandler
                it = sm.measure_until(t_dwell=args["v_dwell"], cb=dh.handle_data)
                db.eid = None  # unregister event id
                db.complete_event(eid)  # mark ss event as done
                data += it

        sm.outOn(False)  # it's probably wise to shut off the smu after every pixel
        pass
        # Fabric.select_pixel(mc, mux_string=f's{q_item[smu_index]["sub_name"]}0')  # disconnect this substrate

        return data

    @staticmethod
    def record_spectrum(ss: LightAPI, outq: Queue | mQueue, lg: Logger):
        """does spectrum fetching at the start of the standard routine"""
        try:
            # intensity_setpoint = ss.intensity
            intensity_setpoint = ss.active_intensity
            wls, counts = ss.get_spectrum()
            data = [[wl, count] for wl, count in zip(wls, counts)]
            spectrum_dict = {"data": data, "intensity": intensity_setpoint, "timestamp": time.time()}
            outq.put({"topic": "calibration/spectrum", "payload": json.dumps(spectrum_dict), "qos": 2, "retain": True})
            if intensity_setpoint != 100:
                # now do it again to make sure we have a record of the 100% baseline
                # ss.apply_intensity(100)
                ss.set_intensity(100)  # type: ignore # TODO: this bypasses the API, fix that
                wls, counts = ss.get_spectrum()
                data = [[wl, count] for wl, count in zip(wls, counts)]
                spectrum_dict = {"data": data, "intensity": 100, "timestamp": time.time()}
                outq.put({"topic": "calibration/spectrum", "payload": json.dumps(spectrum_dict), "qos": 2, "retain": True})
                # ss.apply_intensity(intensity_setpoint)
                ss.set_intensity(100)  # type: ignore # TODO: this bypasses the API, fix that
        except Exception as e:
            lg.debug(f"Failure to collect spectrum data: {repr(e)}")
            # log the exception's whole call stack for debugging
            tb = traceback.TracebackException.from_exception(e)
            lg.debug("".join(tb.format()))

    def compliance_current_guess(self, area=None, jmax=None, imax=None):
        """Guess what the compliance current should be for i-v-t measurements.
        area in cm^2
        jmax in mA/cm^2
        imax in A (overrides jmax/area calc)
        returns value in A (defaults to 0.025A = 0.5cm^2 * 50 mA/cm^2)
        """
        ret_val = 0.5 * 0.05  # default guess is a 0.5 sqcm device operating at just above the SQ limit for Si
        if imax is not None:
            ret_val = imax
        elif (area is not None) and (jmax is not None):
            ret_val = jmax * area / 1000  # scale mA to A

        # enforce the global current limit
        if ret_val > self.current_limit:
            self.lg.warning("Overcurrent protection kicked in")
            ret_val = self.current_limit

        return ret_val

    @staticmethod
    def select_pixel(pcb: MC | virt.FakeMC, mux_string: list[str] | None = None):
        """manipulates the mux. returns nothing and throws a value error if there was a filaure"""
        if mux_string is None:
            mux_string = ["s"]  # empty call disconnects everything

        # ensure we have a list
        if isinstance(mux_string, str):
            selection = [mux_string]
        else:
            selection = mux_string

        pcb.set_mux(selection)

    def suns_voc(self, duration: float, light: LightAPI, sm: SourcemeterAPI, intensities: typing.List[int], dh):
        """do a suns-Voc measurement"""
        step_time = duration / len(intensities)
        svt = []
        for intensity_setpoint in intensities:
            light.intensity = intensity_setpoint
            if isinstance(sm, virt.FakeSMU):
                sm.intensity = intensity_setpoint / 100  # tell the simulated device how much light it's getting
            svt += sm.measure_until(t_dwell=step_time, cb=dh.handle_data)
        return svt

    def clear_plot(self, kind: str):
        """send a message asking a plot to clear its data"""
        self.outq.put({"topic": f"plotter/{kind}/clear", "payload": json.dumps(""), "qos": 2})

    def get_things_to_measure(self, request):
        """tabulate a list of items to loop through during the measurement"""
        # TODO: return support for inferring layout from pcb adapter resistors

        # int("checkerberrycheddarchew")  # force crash for testing

        config = request["config"]
        args = request["args"]

        center = config["stage"]["experiment_positions"]["solarsim"]
        stuff = args["IV_stuff"]  # dict from dataframe

        # recreate a dataframe from the dict
        stuff = pd.DataFrame.from_dict(stuff)

        run_q = collections.deque()  # TODO: check if this could just be a list

        if "slots" in request:
            # int("checkerberrycheddarchew")  # force crash for testing
            required_cols = ["system_label", "user_label", "layout", "bitmask"]
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
                    system_label = data[col_names.index("system_label")]
                    assert system_label == self  # check system label validity
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
                    datalist.append(system_label)
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
                # use validated slot data to update stuff
                pass

        # build pixel/group queue for the run
        if len(request["config"]["smu"]) > 1:  # multismu case
            for group in request["config"]["substrates"]["device_grouping"]:
                group_dict = {}
                for smu_index, device in enumerate(group):
                    d = device.upper()
                    if d in stuff.sort_string.values:
                        pixel_dict = {}
                        rsel = stuff["sort_string"] == d
                        # if not stuff.loc[rsel]["activated"].values[0]:
                        #    continue  # skip devices that are not selected
                        pixel_dict["label"] = stuff.loc[rsel]["label"].values[0]
                        pixel_dict["layout"] = stuff.loc[rsel]["layout"].values[0]
                        pixel_dict["sub_name"] = stuff.loc[rsel]["system_label"].values[0]
                        pixel_dict["device_label"] = stuff.loc[rsel]["device_label"].values[0]
                        mux_index = stuff.loc[rsel]["mux_index"].values[0]
                        assert mux_index is not None, f"{mux_index is not None=}"  # catch error case
                        pixel_dict["pixel"] = int(mux_index)
                        loc = stuff.loc[rsel]["loc"].values[0]
                        assert loc is not None, f"{loc is not None=}"  # catch error case
                        pos = [a + b for a, b in zip(center, loc)]
                        pixel_dict["pos"] = pos
                        pixel_dict["mux_string"] = stuff.loc[rsel]["mux_string"].values[0]

                        area = stuff.loc[rsel]["area"].values[0]
                        if area == -1:  # handle custom area
                            pixel_dict["area"] = args["a_ovr_spin"]
                        else:
                            pixel_dict["area"] = area

                        dark_area = stuff.loc[rsel]["dark_area"].values[0]
                        if dark_area == -1:  # handle custom dark area
                            pixel_dict["dark_area"] = args["a_ovr_spin"]
                        else:
                            pixel_dict["dark_area"] = dark_area

                        group_dict[smu_index] = pixel_dict
                if len(group_dict) > 0:
                    run_q.append(group_dict)
        else:  # single smu case
            # here we build up the pixel handling queue by iterating
            # through the rows of a pandas dataframe
            # that contains one row for each turned on pixel
            for things in stuff.to_dict(orient="records"):
                pixel_dict = {}
                pixel_dict["label"] = things["label"]
                pixel_dict["layout"] = things["layout"]
                pixel_dict["sub_name"] = things["system_label"]
                pixel_dict["device_label"] = things["device_label"]
                pixel_dict["pixel"] = int(things["mux_index"])
                loc = things["loc"]
                pos = [a + b for a, b in zip(center, loc)]
                pixel_dict["pos"] = pos
                if things["area"] == -1:  # handle custom area
                    pixel_dict["area"] = args["a_ovr_spin"]
                else:
                    pixel_dict["area"] = things["area"]
                pixel_dict["mux_string"] = things["mux_string"]
                run_q.append({0: pixel_dict})

        return run_q

    def estop(self, request):
        """emergency stop of the stage"""
        if request["pcb_virt"]:
            ThisMC = virt.FakeMC
        else:
            ThisMC = MC
        with ThisMC(request["pcb"]) as mc:
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
