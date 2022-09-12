#!/usr/bin/env python3

import collections
import concurrent.futures
import os
import signal
import time
import traceback
import hmac
import humanize
import datetime
import typing
import contextlib
import json
import logging
import numpy as np
import pandas as pd
import multiprocessing
import threading

from threading import Event as tEvent
from multiprocessing.synchronize import Event as mEvent

from queue import SimpleQueue as Queue
from multiprocessing.queues import SimpleQueue as mQueue

from slothdb.dbsync import SlothDBSync as SlothDB
from slothdb import enums as en

from centralcontrol import illumination, mppt, sourcemeter
from centralcontrol.mqtt import MQTTClient

from centralcontrol import virt
from centralcontrol.mc import MC
from centralcontrol.motion import Motion


try:
    from centralcontrol.logstuff import get_logger as getLogger
except:
    from logging import getLogger


class DataHandler(object):
    """Handler for measurement data."""

    kind = ""
    sweep = ""
    dbputter: None | typing.Callable[[list[tuple[float, float, float, int]], bool | None], None] = None

    def __init__(self, pixel: dict, outq: mQueue):
        """Construct data handler object.

        Parameters
        ----------
        pixel : dict
            Pixel information.
        """
        self.pixel = pixel
        self.outq = outq

    def handle_data(self, data: list[tuple[float, float, float, int]], dodb: bool = True):
        """Handle measurement data.

        Parameters
        ----------
        data : array-like
            Measurement data.
        """
        payload = {"data": data, "pixel": self.pixel, "sweep": self.sweep}
        if self.dbputter and dodb:
            self.dbputter(data, None)
        self.outq.put({"topic": f"data/raw/{self.kind}", "payload": json.dumps(payload), "qos": 2})


class Fabric(object):
    """High level experiment control logic"""

    current_limit = 0.1  # always safe default

    # listen to/set this to end everything
    killer: tEvent | mEvent

    # the long running work is done in here
    process = multiprocessing.Process()

    # process killer signal
    pkiller = multiprocessing.Event()

    # for outgoing messages
    outq: Queue | mQueue

    # special message output queue so that messages can emrge from forked processes
    poutq = multiprocessing.SimpleQueue()

    # mqtt connection details
    # set mqttargs["host"] externally before calling run() to use mqtt comms
    mqttargs = {"host": None, "port": 1883}
    hk = "gosox".encode()

    # threads/processes
    workers: list[threading.Thread | multiprocessing.Process] = []

    exitcode = 0

    def __init__(self, use_threads: bool = True):
        # self.software_revision = __version__
        # print("Software revision: {:s}".format(self.software_revision))

        self.lg = getLogger(".".join([__name__, type(self).__name__]))  # setup logging
        if use_threads:
            self.killer = tEvent()
        else:  # processes
            self.killer = multiprocessing.Event()

        self.lg.debug("Initialized.")

    @contextlib.contextmanager
    def context(self, comms: MQTTClient):
        # hook up the comms output queue
        self.outq = comms.outq

        # hook into the comms logger
        for handler in comms.lg.handlers:
            if handler.name == "remote":
                self.lg.addHandler(handler)
                break

        if isinstance(self.killer, tEvent):  # use threads
            # launch message handler
            self.workers.append(threading.Thread(target=self.msg_handler, args=(comms.inq,), daemon=False))  # TODO: check if false here causes hangs
            self.workers[-1].start()

            # launch output queue translator
            self.workers.append(threading.Thread(target=self.translate_outqs, daemon=True))
            self.workers[-1].start()
        else:  # use processes
            # TODO: handle running with processes
            self.pkiller.set()
            self.killer.set()

        try:
            yield self
        finally:
            # invoke the killers (if not already done)
            self.pkiller.set()
            self.killer.set()

            if isinstance(self.killer, tEvent):  # use threads
                # clean up threads
                comms.inq.put("die")  # ask msg_handler thread to die
                self.poutq.put("die")  # ask queue translater to die
                for worker in self.workers:
                    worker.join(1.0)
            self.workers = []

    def run(self) -> int:
        """runs the measurement server. blocks forever"""
        commcls = None
        comms_args = None
        if self.mqttargs["host"] is not None:
            comms_args = self.mqttargs
            commcls = MQTTClient
        assert commcls is not None
        assert comms_args is not None
        with commcls(**comms_args) as comms:
            with self.context(comms) as ctx:
                ctx.killer.wait()  # wait here until somebody kills us
        return self.exitcode

    def translate_outqs(self):
        """bridges process queue output messages to comms threading queue messages"""
        while not self.killer.is_set():
            msg = self.poutq.get()
            if msg == "die":
                break
            else:
                self.outq.put(msg)

    def msg_handler(self, inq: mQueue | Queue):
        """handle new messages as they come in from comms"""
        while not self.killer.is_set():
            msg = inq.get()
            if msg == "die":
                break
            else:
                try:
                    request = json.loads(msg.payload.decode())
                    action = msg.topic.split("/")[-1]

                    # perform a requested action
                    if action == "run":
                        self.start_process(self.do_run, (request,))
                    elif action == "stop":
                        self.stop_process()
                    elif action == "quit":
                        self.pkiller.set()
                        self.killer.set()
                        break

                except Exception as e:
                    self.lg.debug(f"Caught a high level exception while handling a request message: {e}")

    def do_run(self, request):
        """handle a request published to the 'run' topic"""
        try:
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
                                    try:
                                        self.lg.setLevel(rundata["config"]["meta"]["internal_loglevel"])
                                    except:
                                        self.lg.setLevel(logging.INFO)
                                    if "slots" in request:  # shoehorn in unvalidated slot info loaded from a tsv file
                                        rundata["slots"] = request["slots"]
                                    self.lg.log(29, "Starting run...")
                                    try:
                                        i_limits = [x["current_limit"] for x in request["config"]["smu"]]
                                        i_limit = min(i_limits)
                                    except Exception as e:
                                        i_limit = 0.1  # use this default if we can't work out a limit from the configuration
                                    self.current_limit = i_limit
                                    things_to_measure = self.get_things_to_measure(rundata)
                                    self.standard_routine(things_to_measure, rundata)
                                    self.lg.log(29, "Run complete!")
        except KeyboardInterrupt:
            pass
        except Exception as e:
            self.lg.error(f"Exception encountered during run: {e}")
            traceback.print_exc()

        self.outq.put({"topic": "measurement/status", "payload": json.dumps("Ready"), "qos": 2, "retain": True})

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

    def select_pixel(self, mux_string=None, pcb=None):
        """manipulates the mux. returns nothing and throws a value error if there was a filaure"""
        if pcb is not None:
            if mux_string is None:
                mux_string = ["s"]  # empty call disconnects everything

            # ensure we have a list
            if isinstance(mux_string, str):
                selection = [mux_string]
            else:
                selection = mux_string

            pcb.set_mux(selection)

    def standard_routine(self, run_queue, request):
        """perform the normal measurement routine on a given list of pixels"""

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
            if "address" in config["stage"]:
                mo_address = config["stage"]["address"]
            # check if the motion controlller is enabled
            if "enabled" in config["stage"]:
                if config["stage"]["enabled"] == True:
                    mo_enabled = True
                    # check args for override of stage enable
                    if ("enable_stage" in args) and (args["enable_stage"] == False):
                        mo_enabled = False

        smucfgs = request["config"]["smu"]  # the smu configs
        for smucfg in smucfgs:
            smucfg["print_speep_deets"] = request["args"]["print_sweep_deets"]  # apply sweep details setting
        sscfg = request["config"]["solarsim"]  # the solar sim config
        sscfg["active_recipe"] = request["args"]["light_recipe"]  # throw in recipe
        sscfg["intensity"] = request["args"]["light_recipe_int"]  # throw in configured intensity

        with contextlib.ExitStack() as stack:  # big context manager
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

            smus = [stack.enter_context(sourcemeter.factory(smucfg)(**smucfg)) for smucfg in smucfgs]  # init and connect to smus
            ss = stack.enter_context(illumination.factory(sscfg)(**sscfg))  # init and connect to solar sim

            rid = db.new_run(uid, site=config["setup"]["site"], setup=config["setup"]["name"], name=args["run_name_prefix"])  # register a new run
            for sm in smus:
                sm.killer = self.pkiller  # register the kill signal
            mppts = [mppt.mppt(sm) for sm in smus]  # spin up all the max power point trackers

            self.send_spectrum(ss)  # record spectrum data

            mc_args = {}
            mc_args["timeout"] = 5
            mc_args["address"] = mc_address
            mc_args["expected_muxes"] = mc_expected_muxes
            mc_args["enabled"] = mc_enabled

            with ThisMC(**mc_args) as mc:
                if fake_mo:
                    mo = Motion(mo_address, pcb_object=virt.FakeMC(), enabled=mo_enabled)
                else:
                    mo = Motion(mo_address, pcb_object=mc, enabled=mo_enabled)

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

                # set NPLC
                if args["nplc"] != -1:
                    [sm.setNPLC(args["nplc"]) for sm in smus]

                # deselect all pixels
                self.select_pixel(mux_string="s", pcb=mc)

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
                remaining = p_total
                n_done = 0
                t0 = time.time()

                while (remaining > 0) and (not self.pkiller.is_set()):
                    q_item = run_queue.popleft()

                    dt = time.time() - t0
                    if (n_done > 0) and (args["cycles"] != 0):
                        tpp = dt / n_done  # recalc time per pixel
                        finishtime = time.time() + tpp * remaining
                        finish_str = datetime.datetime.fromtimestamp(finishtime).strftime("%I:%M%p")
                        human_str = humanize.naturaltime(datetime.datetime.fromtimestamp(finishtime))
                        fraction = n_done / p_total
                        text = f"[{n_done+1}/{p_total}] finishing at {finish_str}, {human_str}"
                        progress_msg = {"text": text, "fraction": fraction}
                        self.outq.put({"topic": "progress", "payload": json.dumps(progress_msg), "qos": 2})

                    n_parallel = len(q_item)  # how many pixels this group holds
                    dev_labels = [val["device_label"] for key, val in q_item.items()]
                    print_label = " + ".join(dev_labels)
                    theres = np.array([val["pos"] for key, val in q_item.items()])
                    self.outq.put({"topic": "plotter/live_devices", "payload": json.dumps(dev_labels), "qos": 2, "retain": True})
                    if n_parallel > 1:
                        there = tuple(theres.mean(0))  # the average location of the group
                    else:
                        there = theres[0]

                    self.lg.log(29, f"[{n_done+1}/{p_total}] Operating on {print_label}")

                    # set up light source voting/synchronization (if any)
                    ss.n_sync = n_parallel

                    # move stage
                    if mo is not None:
                        if (there is not None) and (float("inf") not in there) and (float("-inf") not in there):
                            # force light off for motion if configured
                            if "off_during_motion" in config["solarsim"]:
                                if config["solarsim"]["off_during_motion"] is True:
                                    ss.apply_intensity(0)
                            mo.goto(there)  # command the stage

                    # select pixel(s)
                    pix_selection_strings = [val["mux_string"] for key, val in q_item.items()]
                    self.select_pixel(mux_string=pix_selection_strings, pcb=mc)

                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(smus)) as executor:
                        futures = {}

                        for smu_index, pixel in q_item.items():
                            # setup data handler
                            dh = DataHandler(pixel=pixel, outq=self.poutq)

                            # get or estimate compliance current
                            compliance_i = self.compliance_current_guess(area=pixel["area"], jmax=args["jmax"], imax=args["imax"])
                            dark_compliance_i = self.compliance_current_guess(area=pixel["dark_area"], jmax=args["jmax"], imax=args["imax"])

                            smus[smu_index].area = pixel["area"]  # type: ignore # set virtual smu scaling

                            # submit for processing
                            futures[smu_index] = executor.submit(self.do_iv, rid, ss, smus[smu_index], mppts[smu_index], dh, compliance_i, dark_compliance_i, args, config, sweeps, pixel["area"], pixel["dark_area"])

                        # collect the results
                        for smu_index, future in futures.items():
                            data = future.result()
                            smus[smu_index].outOn(False)  # it's probably wise to shut off the smu after every pixel
                            self.select_pixel(mux_string=f's{q_item[smu_index]["sub_name"]}0', pcb=mc)  # disconnect this substrate

                    n_done += 1
                    remaining = len(run_queue)
                    if (remaining == 0) and (args["cycles"] == 0):
                        run_queue = start_q.copy()
                        remaining = len(run_queue)
                        # refresh the deque to loop forever

                progress_msg = {"text": "Done!", "fraction": 1}
                self.outq.put({"topic": "progress", "payload": json.dumps(progress_msg), "qos": 2})
                self.outq.put({"topic": "plotter/live_devices", "payload": json.dumps([]), "qos": 2, "retain": True})

            db.complete_run(rid)  # mark run as complete
            db.vac()  # mantain db

            # don't leave the light on!
            ss.apply_intensity(0)

    def send_spectrum(self, ss):
        try:
            intensity_setpoint = int(ss.get_intensity())
            wls, counts = ss.get_spectrum()
            data = [[wl, count] for wl, count in zip(wls, counts)]
            spectrum_dict = {"data": data, "intensity": intensity_setpoint, "timestamp": time.time()}
            self.outq.put({"topic": "calibration/spectrum", "payload": json.dumps(spectrum_dict), "qos": 2, "retain": True})
            if intensity_setpoint != 100:
                # now do it again to make sure we have a record of the 100% baseline
                ss.set_intensity(100)
                wls, counts = ss.get_spectrum()
                data = [[wl, count] for wl, count in zip(wls, counts)]
                spectrum_dict = {"data": data, "intensity": 100, "timestamp": time.time()}
                self.outq.put({"topic": "calibration/spectrum", "payload": json.dumps(spectrum_dict), "qos": 2, "retain": True})
                ss.set_intensity(intensity_setpoint)
        except Exception as e:
            self.lg.debug("Failure to collect spectrum data: {e}")

    def do_iv(self, rid, ss, sm, mppt, dh, compliance_i, dark_compliance_i, args, config, sweeps, area, dark_area):
        data = []
        """parallelizable I-V tasks for use in threads"""
        with SlothDB(db_uri=config["db"]["uri"]) as db:
            dh.dbputter = db.putsmdat
            mppt.current_compliance = compliance_i

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

                sm.setupDC(**ss_args)  # initialize the SMU hardware for a steady state measurement

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
                    self._clear_plot("vt_measurement")

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
                self._clear_plot("vt_measurement")
                # db prep
                eid = db.new_event(rid, en.Event.SS, sm.address)  # register new ss event
                deets = {"label": dh.pixel["device_label"], "slot": f'{dh.pixel["sub_name"]}{dh.pixel["pixel"]}', "area": area}
                deets["fixed"] = en.Fixed.CURRENT
                deets["setpoint"] = ss_args["setPoint"]
                db.upsert(f"{db.schema}.tbl_ss_events", deets, eid)  # save event details
                db.eid = eid  # register event id for datahandler
                vt = sm.measureUntil(t_dwell=args["i_dwell"], cb=dh.handle_data)
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
                    self._clear_plot("vt_measurement")
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
                    sm.intensity = 1  # reset the simulated device's intensity
            else:
                ssvoc = None

            # perform sweeps
            for sweep in sweeps:
                if self.pkiller.is_set():
                    self.lg.debug("Killed by killer.")
                    return data
                self.lg.debug(f"Performing first {sweep} sweep (from {args['sweep_start']}V to {args['sweep_end']}V)")
                # sweeps may or may not need light
                if sweep == "dark":
                    ss.lit = False
                    sm.dark = True
                    sweep_current_limit = dark_compliance_i
                else:
                    ss.lit = True
                    sm.dark = False
                    sweep_current_limit = compliance_i
                    dh.kind = "iv_measurement/1"  # TODO: check if this /1 is still needed
                    dh.sweep = sweep
                    self._clear_plot("iv_measurement")

                sweep_args = {}
                sweep_args["sourceVoltage"] = True
                sweep_args["senseRange"] = "f"
                sweep_args["compliance"] = sweep_current_limit
                sweep_args["nPoints"] = int(args["iv_steps"])
                sweep_args["stepDelay"] = args["source_delay"] / 1000
                sweep_args["start"] = args["sweep_start"]
                sweep_args["end"] = args["sweep_end"]
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
                db.putsmdat(iv1, eid)
                db.complete_event(eid)  # mark event as done
                dh.handle_data(iv1, dodb=False)
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
                    db.putsmdat(iv2, eid)
                    db.complete_event(eid)  # mark event as done
                    dh.handle_data(iv2, dodb=False)
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
                self._clear_plot("mppt_measurement")

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
                        dh.handle_data([d])
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
                self._clear_plot("it_measurement")

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
                it = sm.measureUntil(t_dwell=args["v_dwell"], cb=dh.handle_data)
                db.eid = None  # unregister event id
                db.complete_event(eid)  # mark ss event as done
                data += it

        return data

    def suns_voc(self, duration: float, light, sm, intensities: typing.List[int], dh):
        """do a suns-Voc measurement"""
        step_time = duration / len(intensities)
        svt = []
        for intensity_setpoint in intensities:
            light.intensity = intensity_setpoint
            sm.intensity = intensity_setpoint / 100  # tell the simulated device how much light it's getting
            svt += sm.measureUntil(t_dwell=step_time, cb=dh.handle_data)
        return svt

    def _clear_plot(self, kind):
        """Publish measurement data.

        Parameters
        ----------
        kind : str
            Kind of measurement data. This is used as a sub-channel name.
        mqttqp : MQTTQueuePublisher
            MQTT queue publisher object that publishes measurement data.
        """
        self.outq.put({"topic": f"plotter/{kind}/clear", "payload": json.dumps(""), "qos": 2})

    def get_things_to_measure(self, request):
        """tabulate a list of items to loop through during the measurement"""
        # TODO: return support for inferring layout from pcb adapter resistors
        config = request["config"]
        args = request["args"]

        center = config["stage"]["experiment_positions"]["solarsim"]
        stuff = args["IV_stuff"]  # dict from dataframe

        # recreate a dataframe from the dict
        stuff = pd.DataFrame.from_dict(stuff)

        run_q = collections.deque()  # TODO: check if this could just be a list

        if "slots" in request:
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
                self.lg.error(f"Failed processing user-crafted slot table data: {e}")
                return run_q  # send up an empty queue
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
                        assert mux_index is not None  # catch error case
                        pixel_dict["pixel"] = int(mux_index)
                        loc = stuff.loc[rsel]["loc"].values[0]
                        assert loc is not None  # catch error case
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

    def stop_process(self):
        """Abort the running process with increasing meanness until success"""

        if self.process.is_alive():
            self.lg.debug("Setting killer")
            self.pkiller.set()  # ask extremely nicely for the process to come to conclusion
            self.process.join(5.0)  # give it this long to comply

            # up one level in intesnity
            try:
                if self.process.is_alive():
                    self.process.terminate()  # politely tell the process to end
                    self.process.join(2.0)
            except:
                pass

            # up one level in intesnity
            try:
                if self.process.is_alive():
                    if self.process.pid:
                        os.kill(self.process.pid, signal.SIGINT)  # forcefully interrupt the process
                        self.process.join(2.0)
                        self.lg.debug(f"Had to try to kill {self.process.pid=} via SIGINT")
            except:
                pass

            # up one level in intesnity
            try:
                if self.process.is_alive():
                    self.process.kill()  # kill the process
                    self.process.join(2.0)
                    self.lg.debug(f"Had to try to kill {self.process.pid=} via SIGKILL")
            except:
                pass

            self.pkiller.clear()
            self.lg.debug(f"{self.process.is_alive()=}")
            self.lg.log(29, "Request to stop completed!")
            self.outq.put({"topic": "measurement/status", "payload": json.dumps("Ready"), "qos": 2, "retain": True})
        else:
            self.lg.warning("Nothing to stop. Measurement server is idle.")

    def start_process(self, target, args):
        """Start a new process to perform an action if no process is running.

        Parameters
        ----------
        target : function handle
            Function to run in child process.
        args : tuple
            Arguments required by the function.
        """

        if self.process.is_alive():
            self.lg.warning("Measurement server busy!")
        else:
            self.process = multiprocessing.Process(target=target, args=args, daemon=False)  # TODO: check if false here causes hangs
            self.process.start()
            self.outq.put({"topic": "measurement/status", "payload": json.dumps("Busy"), "qos": 2, "retain": True})
