#!/usr/bin/env python3
"""MQTT Server for interacting with the system."""

from pathlib import Path
import sys
import argparse
import collections
import multiprocessing
import concurrent.futures
import threading
import os
import pickle
import queue
import signal
import time
import traceback
import uuid
import humanize
import datetime
import numpy as np

import logging
# for logging directly to systemd journal if we can
try:
  import systemd.journal
except ImportError:
  pass

import paho.mqtt.client as mqtt

# this boilerplate code allows this module to be run directly as a script
if (__name__ == "__main__") and (__package__ in [None, '']):
  __package__ = "centralcontrol"
  # get the dir that holds __package__ on the front of the search path
  sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .fabric import fabric


class DataHandler(object):
  """Handler for measurement data."""
  def __init__(self, kind="", pixel={}, sweep="", outq=None):
    """Construct data handler object.

        Parameters
        ----------
        kind : str
            Kind of measurement data. This is used as a sub-channel name.
        pixel : dict
            Pixel information.
        sweep : {"", "dark", "light"}
            If the handler is for sweep data, specify whether the sweep is under
            "light" or "dark" conditions.
        mqttc : mqtt client
        """
    self.kind = kind
    self.pixel = pixel
    self.sweep = sweep
    self.outq = outq

  def handle_data(self, data):
    """Handle measurement data.

        Parameters
        ----------
        data : array-like
            Measurement data.
        """
    payload = {"data": data, "pixel": self.pixel, "sweep": self.sweep}
    self.outq.put({"topic": f"data/raw/{self.kind}", "payload": pickle.dumps(payload), "qos": 2})


class MQTTServer(object):
  # for outgoing messages
  outq = multiprocessing.Queue()

  # for incoming messages
  msg_queue = queue.Queue()

  # long tasks get their own process
  process = multiprocessing.Process()

  # signal that tells stuff to die
  # can be switched to threading.Event()
  # if we stop using the multiprocessing module
  killer = multiprocessing.Event()

  class Dummy(object):
    pass

  def __init__(self):
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
      if 'systemd' in sys.modules:
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

    # get command line arguments
    self.cli_args = self.get_args()

    # create mqtt client id
    self.client_id = f"measure-{uuid.uuid4().hex}"

    # setup mqtt subscriber client
    self.mqttc = mqtt.Client(client_id=self.client_id)
    self.mqttc.will_set("measurement/status", pickle.dumps("Offline"), 2, retain=True)
    self.mqttc.on_message = self.on_message
    self.mqttc.on_connect = self.on_connect
    self.mqttc.on_disconnect = self.on_disconnect

    self.lg.debug(f"{__name__} initialized.")

  def get_args(self):
    """Get arguments parsed from the command line."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--mqtthost", default="127.0.0.1", const="127.0.0.1", nargs='?', help="IP address or hostname of MQTT broker.")
    return parser.parse_args()

  def start_process(self, target, args):
    """Start a new process to perform an action if no process is running.

      Parameters
      ----------
      target : function handle
          Function to run in child process.
      args : tuple
          Arguments required by the function.
      """

    if self.process.is_alive() == False:
      self.process = multiprocessing.Process(target=target, args=args, daemon=True)
      self.process.start()
      self.outq.put({"topic": "measurement/status", "payload": pickle.dumps("Busy"), "qos": 2, "retain": True})
    else:
      self.lg.warn("Measurement server busy!")

  def stop_process(self):
    """Stop a running process."""

    if self.process.is_alive() == True:
      self.lg.debug("Setting killer")
      self.killer.set()
      join_timeout = 5  # give the killer signal this many seconds to do this gracefully
      self.process.join(join_timeout)
      if self.process.is_alive():
        os.kill(self.process.pid, signal.SIGINT)  # go one level up in abrasiveness
        self.lg.debug(f"Had to try to kill {self.process.pid=} via SIGINT")
      self.killer.clear()
      self.lg.debug(f"{self.process.is_alive()=}")
      self.lg.info("Request to stop completed!")
      self.outq.put({"topic": "measurement/status", "payload": pickle.dumps("Ready"), "qos": 2, "retain": True})
    else:
      self.lg.warn("Nothing to stop. Measurement server is idle.")

  def _calibrate_eqe(self, request):
    """Measure the EQE reference photodiode.

      Parameters
      ----------
      request : dict
          Request dictionary sent to the server.
      mqtthost : str
          MQTT broker IP address or hostname.
      dummy : bool
          Flag for dummy mode using virtual instruments.
      """

    try:
      with fabric() as measurement:
        i_limits = [x["current_limit"] for x in request["config"]["smu"]]
        measurement.current_limit = min(i_limits)
        # create temporary mqtt client
        self.lg.info("Calibrating EQE...")
        args = request["args"]

        # get pixel queue
        if 'EQE_stuff' in args:
          pixel_queue = self._build_q(request, experiment="eqe")

          if len(pixel_queue) > 1:
            self.lg.warn(f"Only one diode can be calibrated at a time, but {len(pixel_queue)} were given. Only the first diode will be measured.")

            # only take first pixel for a calibration
            pixel_dict = pixel_queue[0]
            pixel_queue = collections.deque(maxlen=1)
            pixel_queue.append(pixel_dict)
        else:
          # if it's empty, assume cal diode is connected externally
          pixel_dict = {"label": "external", "device_label": "external", "layout": None, "sub_name": None, "pixel": 0, "pos": None, "area": None, "mux_string": None}
          pixel_queue = collections.deque()
          pixel_queue.append(pixel_dict)

        self._eqe(pixel_queue, request, measurement, calibration=True)

      self.lg.info("EQE calibration complete!")
    except KeyboardInterrupt:
      pass
    except Exception as e:
      traceback.print_exc()
      self.lg.error("EQE CALIBRATION ABORTED! " + str(e))

      self.outq.put({"topic": "measurement/status", "payload": pickle.dumps("Ready"), "qos": 2, "retain": True})

  def _calibrate_psu(self, request):
    """Measure the reference photodiode as a funtcion of LED current.

      Parameters
      ----------
      request : dict
          Request dictionary sent to the server.
      mqtthost : str
          MQTT broker IP address or hostname.
      dummy : bool
          Flag for dummy mode using virtual instruments.
      """

    try:
      with fabric() as measurement:
        i_limits = [x["current_limit"] for x in request["config"]["smu"]]
        measurement.current_limit = min(i_limits)

        self.lg.info("Calibration LED PSU...")

        config = request["config"]
        args = request["args"]

        # get pixel queue
        if 'EQE_stuff' in args:
          pixel_queue = self._build_q(request, experiment="eqe")
        else:
          # if it's empty, assume cal diode is connected externally
          pixel_dict = {"label": args["label_tree"][0], "device_label": "external", "layout": None, "sub_name": None, "pixel": 0, "pos": None, "area": None}
          pixel_queue = collections.deque()
          pixel_queue.append(pixel_dict)

        if request['args']['enable_stage'] == True:
          motion_address = config["stage"]["uri"]
        else:
          motion_address = None

        # the general purpose pcb object is to be virtualized
        gp_pcb_is_fake = config["controller"]["virtual"]
        gp_pcb_address = config["controller"]["address"]

        # the motion pcb object is to be virtualized
        motion_pcb_is_fake = config["stage"]["virtual"]

        # connect instruments
        measurement.connect_instruments(
            visa_lib=config["visa"]["visa_lib"],
            smus=config["smu"],
            pcb_address=gp_pcb_address,
            pcb_virt=gp_pcb_is_fake,
            motion_address=motion_address,
            motion_virt=motion_pcb_is_fake,
            psu_address=config["psu"]["address"],
            psu_virt=config["psu"]["virtual"],
            psu_terminator=config["psu"]["terminator"],
            psu_baud=config["psu"]["baud"],
            psu_ocps=[
                config["psu"]["ch1_ocp"],
                config["psu"]["ch2_ocp"],
                config["psu"]["ch3_ocp"],
            ],
        )

        fake_pcb = measurement.fake_pcb
        inner_pcb = measurement.fake_pcb
        inner_init_args = {}
        if gp_pcb_address is not None:
          if (motion_pcb_is_fake == False) or (gp_pcb_is_fake == False):
            inner_pcb = measurement.real_pcb
            inner_init_args['timeout'] = 1
            inner_init_args['address'] = gp_pcb_address
        with fake_pcb() as fake_p:
          with inner_pcb(**inner_init_args) as inner_p:
            if gp_pcb_is_fake == True:
              gp_pcb = fake_p
            else:
              gp_pcb = inner_p

            if motion_address is not None:
              if motion_pcb_is_fake == gp_pcb_is_fake:
                mo = measurement.motion(motion_address, pcb_object=gp_pcb)
              elif motion_pcb_is_fake == True:
                mo = measurement.motion(motion_address, pcb_object=fake_p)
              else:
                mo = measurement.motion(motion_address, pcb_object=inner_p)
              mo.connect()
            else:
              mo = None

            if args['enable_eqe'] == True:  # we don't need to switch the relay if there is no EQE
              # using smu to measure the current from the photodiode
              resp = measurement.set_experiment_relay("iv", gp_pcb)

              if resp != 0:
                self.lg.error(f"Experiment relay error: {resp}! Aborting run")
                return

            p_total = len(pixel_queue)
            remaining = p_total
            n_done = 0
            t0 = time.time()
            while remaining > 0:
              pixel = pixel_queue.popleft()

              dt = time.time() - t0
              if n_done > 0:
                tpp = dt / n_done
                finishtime = time.time() + tpp * remaining
                finish_str = datetime.datetime.fromtimestamp(finishtime).strftime("%A, %d %B %Y %I:%M%p")
                human_str = humanize.naturaltime(datetime.datetime.fromtimestamp(finishtime))
                fraction = n_done / p_total
                text = f"[{n_done+1}/{p_total}] finishing at {finish_str}, {human_str}"
                progress_msg = {"text": text, "fraction": fraction}
                self.outq.put({"topic": "progress", "payload": pickle.dumps(progress_msg), "qos": 2})

              self.lg.info(f"#### [{n_done+1}/{p_total}] Starting on {pixel['device_label']} ####")

              # move to pixel
              measurement.goto_pixel(pixel, mo)

              measurement.select_pixel(mux_string=pixel['mux_string'], pcb=gp_pcb)

              # perform measurement
              for channel in [1, 2, 3]:
                if config["psu"][f"ch{channel}_ocp"] != 0:
                  psu_calibration = measurement.calibrate_psu(channel, 0.9 * config["psu"][f"ch{channel}_ocp"], 10, config["psu"][f"ch{channel}_voltage"])

                  diode_dict = {"data": psu_calibration, "timestamp": time.time(), "diode": f"{pixel['label']}_device_{pixel['pixel']}"}
                  self.outq.put({"topic": "calibration/psu/ch{channel}", "payload": pickle.dumps(diode_dict), "qos": 2, "retain": True})

              # deselect pixels
              measurement.select_pixel(mux_string='s', pcb=gp_pcb)

              n_done += 1
              remaining = len(pixel_queue)

            progress_msg = {"text": "Done!", "fraction": 1}
            self.outq.put({"topic": "progress", "payload": pickle.dumps(progress_msg), "qos": 2})

      self.lg.info("LED PSU calibration complete!")
    except KeyboardInterrupt:
      pass
    except Exception as e:
      traceback.print_exc()
      self.lg.error("PSU CALIBRATION ABORTED! " + str(e))

    self.outq.put({"topic": "measurement/status", "payload": pickle.dumps("Ready"), "qos": 2, "retain": True})

  def _calibrate_spectrum(self, request):
    """Measure the solar simulator spectrum using it's internal spectrometer.

      Parameters
      ----------
      request : dict
          Request dictionary sent to the server.
      mqtthost : str
          MQTT broker IP address or hostname.
      dummy : bool
          Flag for dummy mode using virtual instruments.
      """

    user_aborted = False
    try:
      with fabric() as measurement:
        i_limits = [x["current_limit"] for x in request["config"]["smu"]]
        measurement.current_limit = min(i_limits)

        self.lg.info("Calibrating solar simulator spectrum...")

        config = request["config"]
        args = request["args"]

        measurement.connect_instruments(
            visa_lib=config["visa"]["visa_lib"],
            light_address=config["solarsim"]["address"],
            light_virt=config["solarsim"]["virtual"],
            light_recipe=args["light_recipe"],
        )
        if hasattr(measurement, 'le'):
          measurement.le.set_intensity(int(args["light_recipe_int"]))

        spectrum = measurement.measure_spectrum()

        spectrum_dict = {"data": spectrum, "timestamp": time.time()}

        # publish calibration
        self.outq.put({"topic": "calibration/spectrum", "payload": pickle.dumps(spectrum_dict), "qos": 2, "retain": True})

      self.lg.info("Finished calibrating solar simulator spectrum!")
    except KeyboardInterrupt:
      user_aborted = True
    except Exception as e:
      traceback.print_exc()
      self.lg.error(f"SPECTRUM CALIBRATION ABORTED! " + str(e))

    self.outq.put({"topic": "measurement/status", "payload": pickle.dumps("Ready"), "qos": 2, "retain": True})

    return user_aborted

  def _build_q(self, request, experiment):
    """Generate a queue of pixels to run through.

      Parameters
      ----------
      args : types.SimpleNamespace
          Experiment arguments.
      experiment : str
          Name used to look up the experiment centre stage position from the config
          file.

      Returns
      -------
      pixel_q : deque
          Queue of pixels to measure.
      """
    # TODO: return support for inferring layout from pcb adapter resistors
    config = request["config"]
    args = request["args"]

    if experiment == "solarsim":
      stuff = args["IV_stuff"]
    elif experiment == "eqe":
      stuff = args["EQE_stuff"]
    else:
      raise (ValueError(f"Unknown experiment: {experiment}"))
    center = config["stage"]["experiment_positions"][experiment]

    # build pixel/group queue for the run
    run_q = collections.deque()
    if (len(request["config"]["smu"]) > 1) and (experiment == "solarsim"):  # multismu case
      for group in request["config"]["substrates"]["device_grouping"]:
        group_dict = {}
        for i, device in enumerate(group):
          d = device.upper()
          if d in stuff.sort_string.values:
            pixel_dict = {}
            rsel = stuff['sort_string'] == d
            pixel_dict['label'] = stuff.loc[rsel]['label'].values[0]
            pixel_dict['layout'] = stuff.loc[rsel]['layout'].values[0]
            pixel_dict['sub_name'] = stuff.loc[rsel]['system_label'].values[0]
            pixel_dict['device_label'] = stuff.loc[rsel]['device_label'].values[0]
            pixel_dict['pixel'] = stuff.loc[rsel]['mux_index'].values[0]
            loc = stuff.loc[rsel]['loc'].values[0]
            pos = [a + b for a, b in zip(center, loc)]
            pixel_dict['pos'] = pos
            area = stuff.loc[rsel]['area'].values[0]
            if area == -1:  # handle custom area
              pixel_dict['area'] = args['a_ovr_spin']
            else:
              pixel_dict['area'] = area
            pixel_dict['mux_string'] = stuff.loc[rsel]['mux_string'].values[0]
            group_dict[i] = pixel_dict
        if len(group_dict) > 0:
          run_q.append(group_dict)
    else:  # single smu case
      # here we build up the pixel handling queue by iterating
      # through the rows of a pandas dataframe
      # that contains one row for each turned on pixel
      for things in stuff.to_dict(orient='records'):
        pixel_dict = {}
        pixel_dict['label'] = things['label']
        pixel_dict['layout'] = things['layout']
        pixel_dict['sub_name'] = things['system_label']
        pixel_dict['device_label'] = things['device_label']
        pixel_dict['pixel'] = things['mux_index']
        loc = things['loc']
        pos = [a + b for a, b in zip(center, loc)]
        pixel_dict['pos'] = pos
        if things['area'] == -1:  # handle custom area
          pixel_dict['area'] = args['a_ovr_spin']
        else:
          pixel_dict['area'] = things['area']
        pixel_dict['mux_string'] = things['mux_string']
        run_q.append(pixel_dict)

    return run_q

  def _clear_plot(self, kind):
    """Publish measurement data.

      Parameters
      ----------
      kind : str
          Kind of measurement data. This is used as a sub-channel name.
      mqttqp : MQTTQueuePublisher
          MQTT queue publisher object that publishes measurement data.
      """
    self.outq.put({"topic": f"plotter/{kind}/clear", "payload": pickle.dumps(""), "qos": 2})

  # send up a log message to the status channel
  def send_log_msg(self, record):
    payload = {"level": record.levelno, "msg": record.msg}
    self.outq.put({"topic": "measurement/log", "payload": pickle.dumps(payload), "qos": 2})

  def do_iv(self, mnt, sm, mppt, dh, compliance_i, args, config, calibration, sweeps):
    """
    parallelizable I-V tasks for use in threads
    """
    measurement = mnt
    mppt.current_compliance = compliance_i

    # "Voc" if
    if args["i_dwell"] > 0:
      if self.killer.is_set():
        self.lg.debug("Killed by killer.")
        return []
      self.lg.info(f"Measuring voltage at constant current for {args['i_dwell']} seconds.")
      # Voc needs light
      if hasattr(measurement, "le"):
        measurement.le.on()

      if calibration == False:
        kind = "vt_measurement"
        dh.kind = kind
        self._clear_plot(kind)

      ss_args = {}
      ss_args["sourceVoltage"] = False
      ss_args["compliance"] = config["ccd"]["max_voltage"]
      ss_args["setPoint"] = args["i_dwell_value"]
      ss_args["senseRange"] = "a"  # NOTE: "a" can possibly cause unknown delays between points

      sm.setupDC(**ss_args)
      vt = sm.measureUntil(t_dwell=args["i_dwell"], cb=dh.handle_data)
      data = vt

      # if this was at Voc, use the last measurement as estimate of Voc
      if (args["i_dwell_value"] == 0) and (len(vt) > 1):
        ssvoc = vt[-1][0]
      else:
        ssvoc = None
    else:
      ssvoc = None

    # perform sweeps
    for sweep in sweeps:
      if self.killer.is_set():
        self.lg.debug("Killed by killer.")
        return data
      self.lg.info(f"Performing first {sweep} sweep (from {args['sweep_start']}V to {args['sweep_end']}V)")
      # sweeps may or may not need light
      if sweep == "dark":
        if hasattr(measurement, "le"):
          measurement.le.off()
      else:
        if hasattr(measurement, "le"):
          measurement.le.on()

      if calibration == False:
        kind = "iv_measurement/1"
        dh.kind = kind
        dh.sweep = sweep
        self._clear_plot("iv_measurement")

      sweep_args = {}
      sweep_args['sourceVoltage'] = True
      sweep_args['senseRange'] = "f"
      sweep_args['compliance'] = compliance_i
      sweep_args['nPoints'] = int(args["iv_steps"])
      sweep_args['stepDelay'] = args["source_delay"] / 1000
      sweep_args['start'] = args["sweep_start"]
      sweep_args['end'] = args["sweep_end"]

      sm.setupSweep(**sweep_args)
      iv1 = sm.measure(sweep_args['nPoints'])
      dh.handle_data(iv1)
      data += iv1

      # register this curve with the mppt
      Pmax_sweep1, Vmpp1, Impp1, maxIx1 = mppt.register_curve(iv1, light=(sweep == "light"))

      if args["return_switch"] == True:
        if self.killer.is_set():
          self.lg.debug("Killed by killer.")
          return data
        self.lg.info(f"Performing second {sweep} sweep (from {args['sweep_end']}V to {args['sweep_start']}V)")

        if calibration == False:
          kind = "iv_measurement/2"
          dh.kind = kind
          dh.sweep = sweep

        sweep_args = {}
        sweep_args['sourceVoltage'] = True
        sweep_args['senseRange'] = "f"
        sweep_args['compliance'] = compliance_i
        sweep_args['nPoints'] = int(args["iv_steps"])
        sweep_args['stepDelay'] = args["source_delay"] / 1000
        sweep_args['start'] = args["sweep_end"]
        sweep_args['end'] = args["sweep_start"]

        sm.setupSweep(**sweep_args)
        iv2 = sm.measure(sweep_args['nPoints'])
        dh.handle_data(iv2)
        data += iv2

        Pmax_sweep2, Vmpp2, Impp2, maxIx2 = mppt.register_curve(iv2, light=(sweep == "light"))

    # TODO: read and interpret parameters for smart mode

    # mppt if
    if args["mppt_dwell"] > 0:
      if self.killer.is_set():
        self.lg.debug("Killed by killer.")
        return data
      self.lg.info(f"Performing max. power tracking for {args['mppt_dwell']} seconds.")
      # mppt needs light
      if hasattr(measurement, "le"):
        measurement.le.on()

      if calibration == False:
        kind = "mppt_measurement"
        dh.kind = kind
        self._clear_plot(kind)

      if ssvoc is not None:
        # tell the mppt what our measured steady state Voc was
        mppt.Voc = ssvoc

      mppt_args = {}
      mppt_args["duration"] = args["mppt_dwell"]
      mppt_args["NPLC"] = args["nplc"]
      mppt_args["extra"] = args["mppt_params"]
      mppt_args["callback"] = dh.handle_data
      mppt_args["voc_compliance"] = config["ccd"]["max_voltage"]
      mppt_args["i_limit"] = compliance_i
      (mt, vt) = mppt.launch_tracker(**mppt_args)
      mppt.reset()

      # reset nplc because the mppt can mess with it
      if args["nplc"] != -1:
        sm.setNPLC(args["nplc"])

      if (calibration == False) and (len(vt) > 0):
        dh.kind = "vtmppt_measurement"
        for d in vt:
          dh.handle_data(d)

      data += vt
      data += mt

    # "J_sc" if
    if args["v_dwell"] > 0:
      if self.killer.is_set():
        self.lg.debug("Killed by killer.")
        return data
      self.lg.info(f"Measuring current at constant voltage for {args['v_dwell']} seconds.")
      # jsc needs light
      if hasattr(measurement, "le"):
        measurement.le.on()

      if calibration == False:
        kind = "it_measurement"
        dh.kind = kind
        self._clear_plot(kind)

      ss_args = {}
      ss_args["sourceVoltage"] = True
      ss_args["compliance"] = compliance_i
      ss_args["setPoint"] = args["v_dwell_value"]
      ss_args["senseRange"] = "a"  # NOTE: "a" can possibly cause unknown delays between points

      sm.setupDC(**ss_args)
      it = sm.measureUntil(t_dwell=args["v_dwell"], cb=dh.handle_data)
      data += it

      return data

  def _ivt(self, run_queue, request, measurement, calibration=False, rtd=False):
    """Run through pixel queue of i-v-t measurements.

      Paramters
      ---------
      pixel_queue : deque of dict
          Queue of dictionaries of pixels to measure.
      request : dict
          Experiment arguments.
      measurement : measurement logic object
          Object controlling instruments and measurements.
      mqttc : MQTTQueuePublisher
          MQTT queue publisher client.
      dummy : bool
          Flag for dummy mode using virtual instruments.
      calibration : bool
          Calibration flag.
      rtd : bool
          RTD flag for type of calibration. Used for reporting.
      """
    config = request["config"]
    args = request["args"]

    if self.killer.is_set():
      self.lg.debug("Killed by killer.")
      return

    if args['enable_stage'] == True:
      motion_address = config["stage"]["uri"]
    else:
      motion_address = None

    if args['enable_solarsim'] == True:
      light_address = config["solarsim"]["address"]
    else:
      light_address = None

    # the general purpose pcb object is to be virtualized
    gp_pcb_is_fake = config["controller"]["virtual"]
    gp_pcb_address = config["controller"]["address"]

    # the motion pcb object is to be virtualized
    motion_pcb_is_fake = config["stage"]["virtual"]

    # connect instruments
    measurement.connect_instruments(
        visa_lib=config["visa"]["visa_lib"],
        smus=config["smu"],
        pcb_address=gp_pcb_address,
        pcb_virt=gp_pcb_is_fake,
        motion_address=motion_address,
        motion_virt=motion_pcb_is_fake,
        light_address=light_address,
        light_virt=config["solarsim"]["virtual"],
        light_recipe=args["light_recipe"],
    )
    if hasattr(measurement, 'le'):
      measurement.le.set_intensity(int(args["light_recipe_int"]))

    fake_pcb = measurement.fake_pcb
    inner_pcb = measurement.fake_pcb
    inner_init_args = {}
    if gp_pcb_address is not None:
      if (motion_pcb_is_fake == False) or (gp_pcb_is_fake == False):
        inner_pcb = measurement.real_pcb
        inner_init_args['timeout'] = 1
        inner_init_args['address'] = gp_pcb_address
    with fake_pcb() as fake_p:
      with inner_pcb(**inner_init_args) as inner_p:
        if gp_pcb_is_fake == True:
          gp_pcb = fake_p
        else:
          gp_pcb = inner_p

        if motion_address is not None:
          if motion_pcb_is_fake == gp_pcb_is_fake:
            mo = measurement.motion(motion_address, pcb_object=gp_pcb)
          elif motion_pcb_is_fake == True:
            mo = measurement.motion(motion_address, pcb_object=fake_p)
          else:
            mo = measurement.motion(motion_address, pcb_object=inner_p)
          mo.connect()
        else:
          mo = None

        if args['enable_eqe'] == True:  # we don't need to switch the relay if there is no EQE
          # set the master experiment relay
          resp = measurement.set_experiment_relay("iv", gp_pcb)

          if resp != 0:
            self.lg.error(f"Experiment relay error: {resp}! Aborting run")
            return

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
          if len(measurement.sms) > 0:
            for sm in measurement.sms:
              sm.setNPLC(args["nplc"])

        # deselect all pixels
        #measurement.select_pixel(mux_string='s', pcb=gp_pcb)  # TODO: figure out why this is flakey
        measurement.select_pixel(mux_string='sa0', pcb=gp_pcb)
        measurement.select_pixel(mux_string='sb0', pcb=gp_pcb)
        measurement.select_pixel(mux_string='sc0', pcb=gp_pcb)
        measurement.select_pixel(mux_string='sd0', pcb=gp_pcb)
        measurement.select_pixel(mux_string='se0', pcb=gp_pcb)
        measurement.select_pixel(mux_string='sf0', pcb=gp_pcb)
        measurement.select_pixel(mux_string='sg0', pcb=gp_pcb)
        measurement.select_pixel(mux_string='sh0', pcb=gp_pcb)

        start_q = run_queue
        if args['cycles'] != 0:
          run_queue *= int(args['cycles'])  # duplicate the pixel_queue "cycles" times
          p_total = len(run_queue)
        else:
          p_total = float('inf')
        remaining = p_total
        n_done = 0
        t0 = time.time()

        # is this a group run queue?
        group_q = True
        if len(run_queue) > 1:
          if "device_label" in run_queue[0]:
            group_q = False

        while (remaining > 0) and (not self.killer.is_set()):
          q_item = run_queue.popleft()

          dt = time.time() - t0
          if (n_done > 0) and (args['cycles'] != 0):
            tpp = dt / n_done  # recalc time per pixel
            finishtime = time.time() + tpp * remaining
            finish_str = datetime.datetime.fromtimestamp(finishtime).strftime("%I:%M%p")
            human_str = humanize.naturaltime(datetime.datetime.fromtimestamp(finishtime))
            fraction = n_done / p_total
            text = f"[{n_done+1}/{p_total}] finishing at {finish_str}, {human_str}"
            progress_msg = {"text": text, "fraction": fraction}
            self.outq.put({"topic": "progress", "payload": pickle.dumps(progress_msg), "qos": 2})

          if group_q == True:
            print_labels = [val['device_label'] for key, val in q_item.items()]
            self.outq.put({"topic": "plotter/live_devices", "payload": pickle.dumps(print_labels), "qos": 2, "retain": True})
            print_label = " + ".join(print_labels)
            theres = np.array([val['pos'] for key, val in q_item.items()])
            there = tuple(theres.mean(0))  # the average location of the group
          else:
            print_label = q_item['device_label']
            there = q_item["pos"]
          self.lg.info(f"#### [{n_done+1}/{p_total}] Starting on {print_label} ####")

          # move stage
          if mo is not None:
            if (there is not None) and (float("inf") not in there) and (float("-inf") not in there):
              # force light off for motion if configured
              if hasattr(measurement, "le") and ('off_during_motion' in config['solarsim']):
                if config['solarsim']['off_during_motion'] == True:
                  measurement.le.off(assume_master=True)
              mo.goto(there)

          # select pixel
          if group_q == True:
            for key, pixel in q_item.items():
              measurement.select_pixel(mux_string=pixel['mux_string'], pcb=gp_pcb)
          else:
            measurement.select_pixel(mux_string=q_item['mux_string'], pcb=gp_pcb)

          with concurrent.futures.ThreadPoolExecutor(max_workers=len(measurement.sms)) as executor:
            futures = {}
            pixels = {}
            for index, sm in enumerate(measurement.sms):
              if group_q == True:
                pixel = q_item[index]
              else:
                pixel = q_item

              pixels[index] = pixel

              # setup data handler
              if calibration == False:
                dh = DataHandler(pixel=pixel, outq=self.outq)
              else:
                dh = self.Dummy()
                dh.handle_data = lambda x: None

              # get or estimate compliance current
              compliance_i = measurement.compliance_current_guess(area=pixel["area"], jmax=args['jmax'], imax=args['imax'])

              # submit for processing
              futures[index] = executor.submit(self.do_iv, measurement, sm, measurement.mppts[index], dh, compliance_i, args, config, calibration, sweeps)

            # collect the datas!
            datas = {}
            for key, future in futures.items():
              data = future.result()
              datas[key] = data
              if calibration == True:
                diode_dict = {"data": data, "timestamp": time.time(), "diode": f"{pixels[key]['label']}_device_{pixels[key]['pixel']}"}
                if rtd == True:
                  self.lg.debug("RTD cal")
                  self.outq.put({"topic": "calibration/rtz", "payload": pickle.dumps(diode_dict), "qos": 2})
                else:
                  self.outq.put({"topic": "calibration/solarsim_diode", "payload": pickle.dumps(diode_dict), "qos": 2})

          # it's probably wise to shut off the smu after every pixel
          if len(measurement.sms) > 0:
            for sm in measurement.sms:
              sm.outOn(False)

          # deselect all pixels
          #measurement.select_pixel(mux_string='s', pcb=gp_pcb)  # TODO: figure out why this is flakey
          measurement.select_pixel(mux_string='sa0', pcb=gp_pcb)
          measurement.select_pixel(mux_string='sb0', pcb=gp_pcb)
          measurement.select_pixel(mux_string='sc0', pcb=gp_pcb)
          measurement.select_pixel(mux_string='sd0', pcb=gp_pcb)
          measurement.select_pixel(mux_string='se0', pcb=gp_pcb)
          measurement.select_pixel(mux_string='sf0', pcb=gp_pcb)
          measurement.select_pixel(mux_string='sg0', pcb=gp_pcb)
          measurement.select_pixel(mux_string='sh0', pcb=gp_pcb)

          n_done += 1
          remaining = len(run_queue)
          if (remaining == 0) and (args['cycles'] == 0):
            run_queue = start_q
            # refresh the deque to loop forever

        progress_msg = {"text": "Done!", "fraction": 1}
        self.outq.put({"topic": "progress", "payload": pickle.dumps(progress_msg), "qos": 2})
        self.outq.put({"topic": "plotter/live_devices", "payload": pickle.dumps([]), "qos": 2, "retain": True})

    # don't leave the light on!
    if hasattr(measurement, "le"):
      measurement.le.off(assume_master=True)

  def _eqe(self, pixel_queue, request, measurement, calibration=False):
    """Run through pixel queue of EQE measurements.

      Paramters
      ---------
      pixel_queue : deque of dict
          Queue of dictionaries of pixels to measure.
      request : dict
          Experiment arguments.
      measurement : measurement logic object
          Object controlling instruments and measurements.
      mqttc : MQTTQueuePublisher
          MQTT queue publisher client.
      dummy : bool
          Flag for dummy mode using virtual instruments.
      calibration : bool
          Calibration flag.
      """
    config = request["config"]
    args = request["args"]

    if args['enable_stage'] == True:
      motion_address = config["stage"]["uri"]
    else:
      motion_address = None

    # the general purpose pcb object is to be virtualized
    gp_pcb_is_fake = config["controller"]["virtual"]
    gp_pcb_address = config["controller"]["address"]

    # the motion pcb object is to be virtualized
    motion_pcb_is_fake = config["stage"]["virtual"]

    # connect instruments
    measurement.connect_instruments(
        visa_lib=config["visa"]["visa_lib"],
        smus=config["smu"],
        pcb_address=gp_pcb_address,
        pcb_virt=gp_pcb_is_fake,
        motion_address=motion_address,
        motion_virt=motion_pcb_is_fake,
        lia_address=config["lia"]["address"],
        lia_virt=config["lia"]["virtual"],
        lia_terminator=config["lia"]["terminator"],
        lia_baud=config["lia"]["baud"],
        lia_output_interface=config["lia"]["output_interface"],
        mono_address=config["monochromator"]["address"],
        mono_virt=config["monochromator"]["virtual"],
        mono_terminator=config["monochromator"]["terminator"],
        mono_baud=config["monochromator"]["baud"],
        psu_address=config["psu"]["address"],
        psu_virt=config["psu"]["virtual"],
    )

    fake_pcb = measurement.fake_pcb
    inner_pcb = measurement.fake_pcb
    inner_init_args = {}
    if gp_pcb_address is not None:
      if (motion_pcb_is_fake == False) or (gp_pcb_is_fake == False):
        inner_pcb = measurement.real_pcb
        inner_init_args['timeout'] = 1
        inner_init_args['address'] = gp_pcb_address
    with fake_pcb() as fake_p:
      with inner_pcb(**inner_init_args) as inner_p:
        if gp_pcb_is_fake == True:
          gp_pcb = fake_p
        else:
          gp_pcb = inner_p

        if motion_address is not None:
          if motion_pcb_is_fake == gp_pcb_is_fake:
            mo = measurement.motion(motion_address, pcb_object=gp_pcb)
          elif motion_pcb_is_fake == True:
            mo = measurement.motion(motion_address, pcb_object=fake_p)
          else:
            mo = measurement.motion(motion_address, pcb_object=inner_p)
          mo.connect()
        else:
          mo = None

        resp = measurement.set_experiment_relay("eqe")
        if resp != 0:
          self.lg.error(f"Experiment relay error: {resp}! Aborting run")
          return

        start_q = pixel_queue
        if args['cycles'] != 0:
          pixel_queue *= int(args['cycles'])
          p_total = len(pixel_queue)
        else:
          p_total = float('inf')
        remaining = p_total
        n_done = 0
        t0 = time.time()
        while remaining > 0:
          pixel = pixel_queue.popleft()

          dt = time.time() - t0
          if n_done > 0:
            tpp = dt / n_done
            finishtime = time.time() + tpp * remaining
            finish_str = datetime.datetime.fromtimestamp(finishtime).strftime("%A, %d %B %Y %I:%M%p")
            human_str = humanize.naturaltime(datetime.datetime.fromtimestamp(finishtime))
            fraction = n_done / p_total
            text = f"[{n_done+1}/{p_total}] finishing at {finish_str}, {human_str}"
            progress_msg = {"text": text, "fraction": fraction}
            self.outq.put({"topic": "progress", "payload": pickle.dumps(progress_msg), "qos": 2})

          self.lg.info(f"#### [{n_done+1}/{p_total}] Starting on {pixel['device_label']} ####")

          # move to pixel
          measurement.goto_pixel(pixel, mo)

          measurement.select_pixel(mux_string=pixel['mux_string'], pcb=gp_pcb)

          self.lg.info(f"Scanning EQE from {args['eqe_start']} nm to {args['eqe_end']} nm")

          compliance_i = measurement.compliance_current_guess(area=pixel["area"], jmax=args['jmax'], imax=args['imax'])

          # if time constant is longer than 1s the instrument aborts its autogain
          # function so need to make sure "user" is used under these conditions
          if ((auto_gain_method := config["lia"]["auto_gain_method"]) == "instr") and (measurement.lia.time_constants[args["eqe_int"]] > 1):
            auto_gain_method = "user"
            self.lg.warn("Instrument autogain cannot be used when time constant > 1s. 'user' autogain setting will be used instead.")

          # determine how live measurement data will be handled
          if calibration == True:
            handler = lambda x: None
          else:
            kind = "eqe_measurement"
            dh = DataHandler(kind=kind, pixel=pixel, outq=self.outq)
            handler = dh.handle_data
            self._clear_plot(kind)

          # perform measurement
          eqe = measurement.eqe(
              psu_ch1_voltage=config["psu"]["ch1_voltage"],
              psu_ch1_current=args["chan1"],
              psu_ch2_voltage=config["psu"]["ch2_voltage"],
              psu_ch2_current=args["chan2"],
              psu_ch3_voltage=config["psu"]["ch3_voltage"],
              psu_ch3_current=args["chan3"],
              smu_voltage=args["eqe_bias"],
              smu_compliance=compliance_i,
              start_wl=args["eqe_start"],
              end_wl=args["eqe_end"],
              num_points=int(args["eqe_step"]),
              grating_change_wls=config["monochromator"]["grating_change_wls"],
              filter_change_wls=config["monochromator"]["filter_change_wls"],
              time_constant=args["eqe_int"],
              auto_gain=True,
              auto_gain_method=auto_gain_method,
              handler=handler,
          )

          # deselect pixels
          measurement.select_pixel(mux_string='s', pcb=gp_pcb)

          # update eqe diode calibration data in
          if calibration == True:
            diode_dict = {"data": eqe, "timestamp": time.time(), "diode": f"{pixel['label']}_device_{pixel['pixel']}"}
            self.outq.put({"topic": "calibration/eqe", "payload": pickle.dumps(diode_dict), "qos": 2, "retain": True})

          n_done += 1
          remaining = len(pixel_queue)
          if (remaining == 0) and (args['cycles'] == 0):
            pixel_queue = start_q
            # refresh the deque to loop forever

        progress_msg = {"text": "Done!", "fraction": 1}
        self.outq.put({"topic": "progress", "payload": pickle.dumps(progress_msg), "qos": 2})

  def _run(self, request):
    """Act on command line instructions.

      Parameters
      ----------
      request : dict
          Request dictionary sent to the server.
      mqtthost : str
          MQTT broker IP address or hostname.
      dummy : bool
          Flag for dummy mode using virtual instruments.
      """
    self.lg.debug("Running measurement...")

    user_aborted = False

    args = request["args"]

    # calibrate spectrum if required
    if ('IV_stuff' in args) and (args['enable_solarsim'] == True):
      user_aborted = self._calibrate_spectrum(request)

    if user_aborted == False:
      try:
        with fabric(killer=self.killer) as measurement:
          self.lg.info("Starting run...")
          i_limits = [x["current_limit"] for x in request["config"]["smu"]]
          measurement.current_limit = min(i_limits)

          if 'IV_stuff' in args:
            q = self._build_q(request, experiment="solarsim")
            self._ivt(q, request, measurement)
            measurement.disconnect_all_instruments()

          if 'EQE_stuff' in args:
            q = self._build_q(request, experiment="eqe")
            self._eqe(q, request, measurement)
            measurement.disconnect_all_instruments()

          # report complete
          self.lg.info("Run complete!")

        print("Measurement complete.")
      except KeyboardInterrupt:
        pass
      except Exception as e:
        traceback.print_exc()
        self.lg.error(f"RUN ABORTED! " + str(e))

      self.outq.put({"topic": "measurement/status", "payload": pickle.dumps("Ready"), "qos": 2, "retain": True})

  # The callback for when a PUBLISH message is received from the server.
  def on_message(self, client, userdata, msg):
    self.msg_queue.put_nowait(msg)  # pass this off for msg_handler to deal with

  # when client connects to broker
  def on_connect(self, client, userdata, flags, rc):
    self.lg.debug(f"mqtt_server connected to broker with result code {rc}")
    client.subscribe("measurement/#", qos=2)
    client.publish("measurement/status", pickle.dumps("Ready"), qos=2, retain=True)

  # when client disconnects from broker
  def on_disconnect(self, client, userdata, rc):
    self.lg.debug(f"Disconnected from broker with result code {rc}")

  def msg_handler(self):
    """Handle MQTT messages in the msg queue.

      This function should run in a separate thread, polling the queue for messages.

      Actions that require instrument I/O run in a worker process. Only one action
      process can run at a time. If an action process is running the server will
      report that it's busy.
      """
    while True:
      msg = self.msg_queue.get()

      try:
        request = pickle.loads(msg.payload)
        action = msg.topic.split("/")[-1]

        # perform a requested action
        if (action == "run") and ((request['args']['enable_eqe'] == True) or (request['args']['enable_iv'] == True)):
          self.start_process(self._run, (request, ))
        elif action == "stop":
          self.stop_process()
        elif (action == "calibrate_eqe") and (request['args']['enable_eqe'] == True):
          self.start_process(self._calibrate_eqe, (request, ))
        elif (action == "calibrate_psu") and (request['args']['enable_psu'] == True) and (request['args']['enable_smu'] == True):
          self.start_process(self._calibrate_psu, (request, ))

      except Exception as e:
        self.lg.debug(f"Caught a high level exception while handling a request message: {e}")

      self.msg_queue.task_done()

  # relays outgoing messages
  def out_relay(self):
    while True:
      to_send = self.outq.get()
      self.mqttc.publish(**to_send).wait_for_publish()  # TODO: test removal of publish wait

  # starts and maintains mqtt broker connection
  def mqtt_connector(self, mqttc):
    while True:
      mqttc.connect(self.cli_args.mqtthost)
      mqttc.loop_forever(retry_first_connection=True)

  def run(self):
    # start the mqtt connector thread
    threading.Thread(target=self.mqtt_connector, args=(self.mqttc, ), daemon=True).start()

    # start the out relay thread
    threading.Thread(target=self.out_relay, daemon=True).start()

    # start the message handler
    self.msg_handler()


def main():
  ms = MQTTServer()
  ms.run()


# required when using multiprocessing in windows, advised on other platforms
if __name__ == "__main__":
  main()
