#!/usr/bin/env python3
"""MQTT Server for interacting with the system."""

from pathlib import Path
import sys
import argparse
import collections
import multiprocessing
import os
import pickle
import queue
import signal
import time
import traceback
import uuid

import paho.mqtt.client as mqtt
import paho.mqtt.publish as publish

from mqtt_tools.queue_publisher import MQTTQueuePublisher

# this boilerplate code allows this module to be run directly as a script
if (__name__ == "__main__") and (__package__ in [None, '']):
  __package__ = "centralcontrol"
  # get the dir that holds __package__ on the front of the search path
  sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .fabric import fabric


def get_args():
  """Get arguments parsed from the command line."""
  parser = argparse.ArgumentParser()
  parser.add_argument("--mqtthost", default="127.0.0.1", help="IP address or hostname of MQTT broker.")
  return parser.parse_args()


def start_process(cli_args, process, target, args):
  """Start a new process to perform an action if no process is running.

    Parameters
    ----------
    target : function handle
        Function to run in child process.
    args : tuple
        Arguments required by the function.
    """

  if process.is_alive() == False:
    ret_proc = multiprocessing.Process(target=target, args=args)
    ret_proc.start()
    publish.single("measurement/status", pickle.dumps("Busy"), qos=2, retain=True, hostname=cli_args.mqtthost)
  else:
    ret_proc = process
    payload = {"level": 30, "msg": "Measurement server busy!"}
    publish.single("measurement/log", pickle.dumps(payload), qos=2, hostname=cli_args.mqtthost)

  return ret_proc


def stop_process(cli_args, process):
  """Stop a running process."""

  if process.is_alive() == True:
    os.kill(process.pid, signal.SIGINT)
    process.join()
    print(f"Process still alive?: {process.is_alive()}")
    payload = {"level": 20, "msg": "Request to stop completed!"}
    publish.single("measurement/log", pickle.dumps(payload), qos=2, hostname=cli_args.mqtthost)
    publish.single("measurement/status", pickle.dumps("Ready"), qos=2, retain=True, hostname=cli_args.mqtthost)
  else:
    payload = {"level": 30, "msg": "Nothing to stop. Measurement server is idle."}
    publish.single("measurement/log", pickle.dumps(payload), qos=2, hostname=cli_args.mqtthost)
  return process


def _calibrate_eqe(request, mqtthost):
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
  print("calibrating eqe...")

  with MQTTQueuePublisher() as mqttc:
    # catch all errors and report back to log
    mqttc.connect(mqtthost)
    mqttc.loop_start()

    try:
      with fabric() as measurement:
        measurement.current_limit = request["config"]["smu"]["current_limit"]
        # create temporary mqtt client
        _log("Calibrating EQE...", 20, mqttc)

        args = request["args"]

        # get pixel queue
        if 'EQE_stuff' in args:
          pixel_queue = _build_q(request, experiment="eqe")

          if len(pixel_queue) > 1:
            _log("Only one diode can be calibrated at a time but " + f"{len(pixel_queue)} were given. Only the first diode will be" + " measured.", 30, mqttc)

            # only take first pixel for a calibration
            pixel_dict = pixel_queue[0]
            pixel_queue = collections.deque(maxlen=1)
            pixel_queue.append(pixel_dict)
        else:
          # if it's empty, assume cal diode is connected externally
          pixel_dict = {"label": "external", "layout": None, "sub_name": None, "pixel": 0, "pos": None, "area": None, "mux_string": None}
          pixel_queue = collections.deque()
          pixel_queue.append(pixel_dict)

        _eqe(pixel_queue, request, measurement, mqttc, calibration=True)

        _log("EQE calibration complete!", 20, mqttc)

      print("EQE calibration finished.")
    except KeyboardInterrupt:
      pass
    except Exception as e:
      traceback.print_exc()
      _log(f"EQE CALIBRATION ABORTED! " + str(e), 40, mqttc)

    mqttc.append_payload("measurement/status", pickle.dumps("Ready"), retain=True)


def _calibrate_psu(request, mqtthost):
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
  print("Calibrating psu...")

  with MQTTQueuePublisher() as mqttc:
    mqttc.connect(mqtthost)
    mqttc.loop_start()
    try:
      with fabric() as measurement:
        measurement.current_limit = request["config"]["smu"]["current_limit"]

        _log("Calibration LED PSU...", 20, mqttc)

        config = request["config"]
        args = request["args"]

        # get pixel queue
        if 'EQE_stuff' in args:
          pixel_queue = _build_q(request, experiment="eqe")
        else:
          # if it's empty, assume cal diode is connected externally
          pixel_dict = {"label": args["label_tree"][0], "layout": None, "sub_name": None, "pixel": 0, "pos": None, "area": None}
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
        measurement.connect_instruments(visa_lib=config["visa"]["visa_lib"], smu_address=config["smu"]["address"], smu_virt=config["smu"]["virtual"], smu_terminator=config["smu"]["terminator"], smu_baud=config["smu"]["baud"], smu_front_terminals=config["smu"]["front_terminals"], smu_two_wire=config["smu"]["two_wire"], pcb_address=gp_pcb_address, pcb_virt=gp_pcb_is_fake, motion_address=motion_address, motion_virt=motion_pcb_is_fake, psu_address=config["psu"]["address"], psu_virt=config["psu"]["virtual"], psu_terminator=config["psu"]["terminator"], psu_baud=config["psu"]["baud"], psu_ocps=[
            config["psu"]["ch1_ocp"],
            config["psu"]["ch2_ocp"],
            config["psu"]["ch3_ocp"],
        ])

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
                _log(f"Experiment relay error: {resp}! Aborting run", 40, mqttc)
                return

            last_label = None
            while len(pixel_queue) > 0:
              pixel = pixel_queue.popleft()
              label = pixel["label"]
              pix = pixel["pixel"]
              _log(f"Operating on substrate {label}, pixel {pix}...", 20, mqttc)

              # add id str to handlers to display on plots
              idn = f"{label}_pixel{pix}"

              #print(pixel)

              # we have a new substrate
              if last_label != label:
                _log(f"New substrate using '{pixel['layout']}' layout!", 20, mqttc)
                last_label = label

              # move to pixel
              #_log(f"Initiating movement to {idn}", 10, mqttc)
              measurement.goto_pixel(pixel, mo)
              #_log(f"Movement complete.", 10, mqttc)

              resp = measurement.select_pixel(pixel["mux_string"], gp_pcb)
              if resp != 0:
                _log(f"Mux error: {resp}! Aborting run!", 40, mqttc)
                break

              timestamp = time.time()

              # perform measurement
              for channel in [1, 2, 3]:
                if config["psu"][f"ch{channel}_ocp"] != 0:
                  psu_calibration = measurement.calibrate_psu(channel, 0.9 * config["psu"][f"ch{channel}_ocp"], 10, config["psu"][f"ch{channel}_voltage"])

                  diode_dict = {"data": psu_calibration, "timestamp": timestamp, "diode": idn}
                  mqttc.append_payload(f"calibration/psu/ch{channel}", pickle.dumps(diode_dict), retain=True)

        _log("LED PSU calibration complete!", 20, mqttc)
      print("Finished calibrating PSU.")
    except KeyboardInterrupt:
      pass
    except Exception as e:
      traceback.print_exc()
      _log(f"PSU CALIBRATION ABORTED! " + str(e), 40, mqttc)

    mqttc.append_payload("measurement/status", pickle.dumps("Ready"), retain=True)


def _calibrate_spectrum(request, mqtthost):
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
  print("Calibrating spectrum...")

  user_aborted = False

  with MQTTQueuePublisher() as mqttc:
    mqttc.connect(mqtthost)
    mqttc.loop_start()

    try:
      with fabric() as measurement:
        measurement.current_limit = request["config"]["smu"]["current_limit"]

        _log("Calibrating solar simulator spectrum...", 20, mqttc)

        config = request["config"]
        args = request["args"]

        measurement.connect_instruments(visa_lib=config["visa"]["visa_lib"], light_address=config["solarsim"]["address"], light_virt=config["solarsim"]["virtual"], light_recipe=args["light_recipe"])
        measurement.le.set_intensity(int(args["light_recipe_int"]))

        timestamp = time.time()

        spectrum = measurement.measure_spectrum()

        spectrum_dict = {"data": spectrum, "timestamp": timestamp}

        # publish calibration
        mqttc.append_payload("calibration/spectrum", pickle.dumps(spectrum_dict), retain=True)

        _log("Finished calibrating solar simulator spectrum!", 20, mqttc)

      print("Spectrum calibration complete.")
    except KeyboardInterrupt:
      user_aborted = True
    except Exception as e:
      traceback.print_exc()
      _log(f"SPECTRUM CALIBRATION ABORTED! " + str(e), 40, mqttc)

    mqttc.append_payload("measurement/status", pickle.dumps("Ready"), retain=True)

  return user_aborted


def _build_q(request, experiment):
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

  # build pixel queue
  pixel_q = collections.deque()
  # here we build up the pixel handling queue by iterating
  # through the rows of a pandas dataframe
  # that contains one row for each turned on pixel
  for things in stuff.to_dict(orient='records'):
    pixel_dict = {}
    pixel_dict['label'] = things['label']
    pixel_dict['layout'] = things['layout']
    pixel_dict['sub_name'] = things['system_label']
    pixel_dict['pixel'] = things['mux_index']
    loc = things['loc']
    pos = [a + b for a, b in zip(center, loc)]
    pixel_dict['pos'] = pos
    if things['area'] == -1:  # handle custom area
      pixel_dict['area'] = args['a_ovr_spin']
    else:
      pixel_dict['area'] = things['area']
    pixel_dict['mux_string'] = things['mux_string']
    pixel_q.append(pixel_dict)
  return pixel_q


class DataHandler:
  """Handler for measurement data."""
  def __init__(self, kind="", pixel={}, sweep="", mqttqp=None):
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
        mqttqp : MQTTQueuePublisher
            MQTT queue publisher object that publishes measurement data.
        """
    self.kind = kind
    self.pixel = pixel
    self.sweep = sweep
    self.mqttqp = mqttqp

  def handle_data(self, data):
    """Handle measurement data.

        Parameters
        ----------
        data : array-like
            Measurement data.
        """
    payload = {
        "data": data,
        "pixel": self.pixel,
        "sweep": self.sweep,
    }
    self.mqttqp.append_payload(f"data/raw/{self.kind}", pickle.dumps(payload))


def _clear_plot(kind, mqttqp):
  """Publish measurement data.

    Parameters
    ----------
    kind : str
        Kind of measurement data. This is used as a sub-channel name.
    mqttqp : MQTTQueuePublisher
        MQTT queue publisher object that publishes measurement data.
    """
  payload = ""
  mqttqp.append_payload(f"plotter/{kind}/clear", pickle.dumps(payload))


def _log(msg, level, mqttqp):
  """Publish info for logging.

    Parameters
    ----------
    msg : str
        Log message.
    level : int
        Log level used by logging module:

            * 50 : CRITICAL
            * 40 : ERROR
            * 30 : WARNING
            * 20 : INFO
            * 10 : DEBUG
            * 0 : NOTSET

    mqttqp : MQTTQueuePublisher
        MQTT queue publisher object that publishes measurement data.
    """
  payload = {"level": level, "msg": msg}
  mqttqp.append_payload("measurement/log", pickle.dumps(payload))


def _ivt(pixel_queue, request, measurement, mqttc, calibration=False, rtd=False):
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
  measurement.connect_instruments(visa_lib=config["visa"]["visa_lib"], smu_address=config["smu"]["address"], smu_virt=config["smu"]["virtual"], smu_terminator=config["smu"]["terminator"], smu_baud=config["smu"]["baud"], smu_front_terminals=config["smu"]["front_terminals"], smu_two_wire=config["smu"]["two_wire"], pcb_address=gp_pcb_address, pcb_virt=gp_pcb_is_fake, motion_address=motion_address, motion_virt=motion_pcb_is_fake, light_address=light_address, light_virt=config["solarsim"]["virtual"], light_recipe=args["light_recipe"])
  measurement.le.set_intensity(int(args["light_recipe_int"]))

  source_delay = args["source_delay"] / 1000  # scale this from ms to s because that's what the SMU wants
  last_label = None

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
          _log(f"Experiment relay error: {resp}! Aborting run", 40, mqttc)
          return

      # scan through the pixels and do the requested measurements
      while len(pixel_queue) > 0:
        # instantiate container for all measurement data on pixel
        data = []

        # get pixel info
        pixel = pixel_queue.popleft()
        label = pixel["label"]
        pix = pixel["pixel"]
        print(f"Operating on substrate {label}, pixel {pix}...")

        print(f"{pixel}")

        # add id str to handlers to display on plots
        idn = f"{label}_pixel{pix}"

        # check if we have a new substrate
        if last_label != label:
          print(f"New substrate using '{pixel['layout']}' layout!")
          last_label = label

        # force light off for motion if configured
        if hasattr(measurement, "le") and ('off_during_motion' in config['solarsim']):
          if config['solarsim']['off_during_motion'] == True:
            measurement.le.off()
        # move to pixel
        measurement.goto_pixel(pixel, mo)

        # select pixel
        resp = measurement.select_pixel(pixel['mux_string'], gp_pcb)
        if resp != 0:
          _log(f"Mux error: {resp}! Aborting run", 40, mqttc)
          break

        # init parameters derived from steadystate measurements
        ssvoc = None

        # get or estimate compliance current
        compliance_i = measurement.compliance_current_guess(area=pixel["area"], jmax=args['jmax'], imax=args['imax'])
        measurement.mppt.current_compliance = compliance_i

        # setup data handler
        if calibration == False:
          dh = DataHandler(pixel=pixel, mqttqp=mqttc)
          handler = dh.handle_data
        else:

          class Dummy:
            pass

          dh = Dummy()
          handler = lambda x: None

        timestamp = time.time()

        # "Voc" if
        if args["i_dwell"] > 0:
          _log(f"Measuring voltage output at constant current on {idn}.", 20, mqttc)
          # Voc needs light
          if hasattr(measurement, "le"):
            measurement.le.on()

          if calibration == False:
            kind = "vt_measurement"
            dh.kind = kind
            _clear_plot(kind, mqttc)

          # constant current dwell step
          vt = measurement.steady_state(
              t_dwell=args["i_dwell"],
              NPLC=args["nplc"],
              sourceVoltage=False,
              compliance=config["ccd"]["max_voltage"],
              senseRange="a",  # NOTE: "a" can possibly cause unknown delays between points
              setPoint=args["i_dwell_value"],
              handler=handler)

          data += vt

          # if this was at Voc, use the last measurement as estimate of Voc
          if args["i_dwell_value"] == 0:
            ssvoc = vt[-1][0]
          else:
            ssvoc = None

        # if performing sweeps
        sweeps = []
        if args["sweep_check"] == True:
          # detmine type of sweeps to perform
          if (s := args["lit_sweep"]) == 0:
            sweeps = ["dark", "light"]
          elif s == 1:
            sweeps = ["light", "dark"]
          elif s == 2:
            sweeps = ["dark"]
          elif s == 3:
            sweeps = ["light"]

        # perform sweeps
        for sweep in sweeps:
          # sweeps may or may not need light
          if sweep == "dark":
            if hasattr(measurement, "le"):
              measurement.le.off()
            sense_range = "f"
          else:
            if hasattr(measurement, "le"):
              measurement.le.on()
            sense_range = "f"

          if args["sweep_check"] == True:
            _log(f"Performing {sweep} sweep 1 on {idn}.", 20, mqttc)
            print(f'Sweeping voltage from {args["sweep_start"]} V to {args["sweep_end"]} V')

            if calibration == False:
              kind = "iv_measurement/1"
              dh.kind = kind
              dh.sweep = sweep
              _clear_plot("iv_measurement", mqttc)

            sweep_args = {}
            sweep_args['sourceVoltage'] = True
            sweep_args['senseRange'] = sense_range
            sweep_args['compliance'] = compliance_i
            sweep_args['nPoints'] = int(args["iv_steps"])
            sweep_args['stepDelay'] = source_delay
            sweep_args['start'] = args["sweep_start"]
            sweep_args['end'] = args["sweep_end"]
            sweep_args['NPLC'] = args["nplc"]
            sweep_args['handler'] = handler
            iv1 = measurement.sweep(**sweep_args)

            data += iv1

            Pmax_sweep1, Vmpp1, Impp1, maxIx1 = measurement.mppt.register_curve(iv1, light=(sweep == "light"))

          if args["return_switch"] == True:
            _log(f"Performing {sweep} sweep 2 on {idn}.", 20, mqttc)
            print(f'Sweeping voltage from {args["sweep_end"]} V to {args["sweep_start"]} V')

            if calibration == False:
              kind = "iv_measurement/2"
              dh.kind = kind
              dh.sweep = sweep

            sweep_args = {}
            sweep_args['sourceVoltage'] = True
            sweep_args['senseRange'] = sense_range
            sweep_args['compliance'] = compliance_i
            sweep_args['nPoints'] = int(args["iv_steps"])
            sweep_args['stepDelay'] = source_delay
            sweep_args['start'] = args["sweep_end"]
            sweep_args['end'] = args["sweep_start"]
            sweep_args['NPLC'] = args["nplc"]
            sweep_args['handler'] = handler
            iv2 = measurement.sweep(**sweep_args)

            data += iv2

            Pmax_sweep2, Vmpp2, Impp2, maxIx2 = measurement.mppt.register_curve(iv2, light=(sweep == "light"))

        # TODO: read and interpret parameters for smart mode
        # # determine Vmpp and current compliance for mppt
        # if (self.args["sweep_check"] == True) & (self.args["return_switch"] == True):
        #     if abs(Pmax_sweep1) > abs(Pmax_sweep2):
        #         Vmpp = Vmpp1
        #         compliance_i = Impp1 * 5
        #     else:
        #         Vmpp = Vmpp2
        #         compliance_i = Impp2 * 5
        # elif self.args["sweep_check"] == True:
        #     Vmpp = Vmpp1
        #     compliance_i = Impp1 * 5
        # else:
        #     # no sweeps have been measured so max power tracker will estimate Vmpp
        #     # based on Voc (or measure it if also no Voc) and will use initial
        #     # compliance set before any measurements were taken.
        #     Vmpp = None
        # self.logic.mppt.Vmpp = Vmpp

        # mppt if
        if args["mppt_dwell"] > 0:
          # mppt needs light
          if hasattr(measurement, "le"):
            measurement.le.on()
          _log(f"Performing max. power tracking on {idn}.", 20, mqttc)
          print(f"Tracking maximum power point for {args['mppt_dwell']} seconds.")

          if calibration == False:
            kind = "mppt_measurement"
            dh.kind = kind
            _clear_plot(kind, mqttc)

          if ssvoc is not None:
            # tell the mppt what our measured steady state Voc was
            measurement.mppt.Voc = ssvoc

          (mt, vt) = measurement.track_max_power(args["mppt_dwell"], NPLC=args["nplc"], extra=args["mppt_params"], voc_compliance=config["ccd"]["max_voltage"], i_limit=compliance_i, handler=handler)

          if (calibration == False) and (len(vt) > 0):
            dh.kind = "vtmppt_measurement"
            for d in vt:
              handler(d)

          data += vt
          data += mt

        # "J_sc" if
        if args["v_dwell"] > 0:
          # jsc needs light
          if hasattr(measurement, "le"):
            measurement.le.on()
          _log(f"Measuring output current and constant voltage on {idn}.", 20, mqttc)

          if calibration == False:
            kind = "it_measurement"
            dh.kind = kind
            _clear_plot(kind, mqttc)

          it = measurement.steady_state(t_dwell=args["v_dwell"], NPLC=args["nplc"], sourceVoltage=True, compliance=compliance_i, senseRange="a", setPoint=args["v_dwell_value"], handler=handler)

          data += it

        # it's probably wise to shut off the smu after every pixel
        measurement.sm.outOn(False)

        if calibration == True:
          diode_dict = {"data": data, "timestamp": timestamp, "diode": idn}
          if rtd == True:
            print("RTD")
            mqttc.append_payload("calibration/rtd", pickle.dumps(diode_dict))
          else:
            mqttc.append_payload("calibration/solarsim_diode", pickle.dumps(diode_dict))

  # don't leave the light on!
  if hasattr(measurement, "le"):
    measurement.le.off()


def _eqe(pixel_queue, request, measurement, mqttc, calibration=False):
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
  measurement.connect_instruments(visa_lib=config["visa"]["visa_lib"], smu_address=config["smu"]["address"], smu_virt=config["smu"]["virtual"], smu_terminator=config["smu"]["terminator"], smu_baud=config["smu"]["baud"], smu_front_terminals=config["smu"]["front_terminals"], smu_two_wire=config["smu"]["two_wire"], pcb_address=gp_pcb_address, pcb_virt=gp_pcb_is_fake, motion_address=motion_address, motion_virt=motion_pcb_is_fake, lia_address=config["lia"]["address"], lia_virt=config["lia"]["virtual"], lia_terminator=config["lia"]["terminator"], lia_baud=config["lia"]["baud"], lia_output_interface=config["lia"]["output_interface"], mono_address=config["monochromator"]["address"], mono_virt=config["monochromator"]["virtual"], mono_terminator=config["monochromator"]["terminator"], mono_baud=config["monochromator"]["baud"], psu_address=config["psu"]["address"], psu_virt=config["psu"]["virtual"])

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
        _log(f"Experiment relay error: {resp}! Aborting run", 40, mqttc)
        return

      last_label = None
      while len(pixel_queue) > 0:
        pixel = pixel_queue.popleft()
        label = pixel["label"]
        pix = pixel["pixel"]

        # add id str to handlers to display on plots
        idn = f"{label}_pixel{pix}"

        _log(f"Measuring EQE on {idn}", 20, mqttc)

        print(f"{pixel}")

        # we have a new substrate
        if last_label != label:
          print(f"New substrate using '{pixel['layout']}' layout!")
          last_label = label

        # move to pixel
        measurement.goto_pixel(pixel, mo)

        resp = measurement.select_pixel(pixel['mux_string'], gp_pcb)
        if resp != 0:
          _log(f"Mux error: {resp}! Aborting run!", 40, mqttc)
          break

        _log(f"Scanning EQE from {args['eqe_start']} nm to {args['eqe_end']} nm", 20, mqttc)

        compliance_i = measurement.compliance_current_guess(area=pixel["area"], jmax=args['jmax'], imax=args['imax'])

        # if time constant is longer than 1s the instrument aborts its autogain
        # function so need to make sure "user" is used under these conditions
        if ((auto_gain_method := config["lia"]["auto_gain_method"]) == "instr") and (measurement.lia.time_constants[args["eqe_int"]] > 1):
          auto_gain_method = "user"
          _log(("Instrument autogain cannot be used when time constant > 1s. 'user'" + " autogain setting will be used instead."), 30, mqttc)

        # determine how live measurement data will be handled
        if calibration == True:
          handler = lambda x: None
        else:
          kind = "eqe_measurement"
          dh = DataHandler(kind=kind, pixel=pixel, mqttqp=mqttc)
          handler = dh.handle_data
          _clear_plot(kind, mqttc)

        # get human-readable timestamp
        timestamp = time.time()

        # perform measurement
        eqe = measurement.eqe(psu_ch1_voltage=config["psu"]["ch1_voltage"], psu_ch1_current=args["chan1"], psu_ch2_voltage=config["psu"]["ch2_voltage"], psu_ch2_current=args["chan2"], psu_ch3_voltage=config["psu"]["ch3_voltage"], psu_ch3_current=args["chan3"], smu_voltage=args["eqe_bias"], smu_compliance=compliance_i, start_wl=args["eqe_start"], end_wl=args["eqe_end"], num_points=int(args["eqe_step"]), grating_change_wls=config["monochromator"]["grating_change_wls"], filter_change_wls=config["monochromator"]["filter_change_wls"], time_constant=args["eqe_int"], auto_gain=True, auto_gain_method=auto_gain_method, handler=handler)

        # update eqe diode calibration data in
        if calibration == True:
          diode_dict = {"data": eqe, "timestamp": timestamp, "diode": idn}
          mqttc.append_payload("calibration/eqe", pickle.dumps(diode_dict), retain=True)


def _run(request, mqtthost):
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
  print("Running measurement...")

  user_aborted = False

  args = request["args"]

  # calibrate spectrum if required
  if ('IV_stuff' in args) and (args['enable_solarsim'] == True):
    user_aborted = _calibrate_spectrum(request, mqtthost)

  if user_aborted == False:
    with MQTTQueuePublisher() as mqttc:
      mqttc.connect(mqtthost)
      mqttc.loop_start()
      try:
        with fabric() as measurement:
          _log("Starting run...", 20, mqttc)
          measurement.current_limit = request["config"]["smu"]["current_limit"]

          if 'IV_stuff' in args:
            q = _build_q(request, experiment="solarsim")
            _ivt(q, request, measurement, mqttc)
            measurement.disconnect_all_instruments()

          if 'EQE_stuff' in args:
            q = _build_q(request, experiment="eqe")
            _eqe(q, request, measurement, mqttc)
            measurement.disconnect_all_instruments()

          # report complete
          _log("Run complete!", 20, mqttc)

        print("Measurement complete.")
      except KeyboardInterrupt:
        pass
      except Exception as e:
        traceback.print_exc()
        _log(f"RUN ABORTED! " + str(e), 40, mqttc)

      mqttc.append_payload("measurement/status", pickle.dumps("Ready"), retain=True)


def on_message(mqttc, obj, msg, msg_queue):
  """Add an MQTT message to the message queue."""
  msg_queue.put_nowait(msg)


def msg_handler(msg_queue, cli_args, process):
  """Handle MQTT messages in the msg queue.

    This function should run in a separate thread, polling the queue for messages.

    Actions that require instrument I/O run in a worker process. Only one action
    process can run at a time. If an action process is running the server will
    report that it's busy.
    """
  while True:
    msg = msg_queue.get()

    try:
      request = pickle.loads(msg.payload)
      action = msg.topic.split("/")[-1]

      # perform a requested action
      if (action == "run") and ((request['args']['enable_eqe'] == True) or (request['args']['enable_iv'] == True)):
        process = start_process(cli_args, process, _run, (request, cli_args.mqtthost))
      elif action == "stop":
        process = stop_process(cli_args, process)
      elif (action == "calibrate_eqe") and (request['args']['enable_eqe'] == True):
        process = start_process(cli_args, process, _calibrate_eqe, (request, cli_args.mqtthost))
      elif (action == "calibrate_psu") and (request['args']['enable_psu'] == True) and (request['args']['enable_smu'] == True):
        process = start_process(cli_args, process, _calibrate_psu, (request, cli_args.mqtthost))

    except:
      pass

    msg_queue.task_done()


def main():
  # get command line arguments
  cli_args = get_args()

  # create dummy process
  process = multiprocessing.Process()

  # queue for storing incoming messages
  msg_queue = queue.Queue()

  # create mqtt client id
  client_id = f"measure-{uuid.uuid4().hex}"

  # setup mqtt subscriber client
  mqttc = mqtt.Client(client_id=client_id)
  mqttc.will_set("measurement/status", pickle.dumps("Offline"), 2, retain=True)
  mqttc.on_message = lambda mqttc, obj, msg: on_message(mqttc, obj, msg, msg_queue)
  mqttc.connect(cli_args.mqtthost)
  mqttc.subscribe("measurement/#", qos=2)
  mqttc.loop_start()

  publish.single("measurement/status", pickle.dumps("Ready"), qos=2, retain=True, hostname=cli_args.mqtthost)

  print(f"{client_id} connected!")

  msg_handler(msg_queue, cli_args, process)


# required when using multiprocessing in windows, advised on other platforms
if __name__ == "__main__":
  main()
