#!/usr/bin/env python3

import paho.mqtt.client as mqtt
import argparse
import pickle
import threading
import queue
import serial  # for monochromator
from pathlib import Path
import sys
import pyvisa
import collections
import numpy as np
import time

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
if (__name__ == "__main__") and (__package__ in [None, '']):
  __package__ = "centralcontrol"
  # get the dir that holds __package__ on the front of the search path
  sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from . import virt
from .motion import motion
from .k2400 import k2400 as sm
from .illumination import illumination
from .pcb import pcb


class UtilityHandler(object):
  # for storing command messages as they arrive
  cmdq = queue.Queue()

  # for storing jobs to be worked on
  taskq = queue.Queue()

  # for outgoing messages
  outputq = queue.Queue()

  def __init__(self, mqtt_server_address='127.0.0.1', mqtt_server_port=1883):
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

    self.lg.debug(f"{__name__} initialized.")

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
    result = {'cmd': ''}
    try:
      msg = pickle.loads(mqtt_msg.payload)
    except Exception as e:
      msg = None
    if isinstance(msg, collections.abc.Iterable):
      if 'cmd' in msg:
        result = msg
    return (result)

  # the manager thread decides if the command should be passed on to the worker or rejected.
  # immediagely handles estops itself
  # this function must be fast and non-blocking to avoid estop service delay
  def manager(self):
    while True:
      cmd_msg = self.filter_cmd(self.cmdq.get())
      self.lg.debug('New command message!')
      if cmd_msg['cmd'] == 'estop':
        if cmd_msg['pcb_virt'] == True:
          tpcb = virt.pcb
        else:
          tpcb = pcb
        try:
          with tpcb(cmd_msg['pcb'], timeout=10) as p:
            p.query('b')
          self.lg.warn('Emergency stop command issued. Re-Homing required before any further movements.')
        except Exception as e:
          emsg = "Unable to emergency stop."
          self.lg.warn(emsg)
          logging.exception(emsg)
      elif (self.taskq.unfinished_tasks == 0):
        # the worker is available so let's give it something to do
        self.taskq.put_nowait(cmd_msg)
      elif (self.taskq.unfinished_tasks > 0):
        self.lg.warn(f'Backend busy (task queue size = {self.taskq.unfinished_tasks}). Command rejected.')
      else:
        self.lg.debug(f'Command message rejected:: {cmd_msg}')
      self.cmdq.task_done()

  # asks for the current stage position and sends it up to /response
  def send_pos(self, mo):
    pos = mo.get_position()
    payload = {'pos': pos}
    payload = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    output = {'destination': 'response', 'payload': payload}  # post the position to the response channel
    self.outputq.put(output)

  # work gets done here so that we don't do any processing on the mqtt network thread
  # can block and be slow. new commands that come in while this is working will just be rejected
  def worker(self):
    while True:
      task = self.taskq.get()
      self.lg.debug(f"New task: {task['cmd']} (queue size = {self.taskq.unfinished_tasks})")
      # handle pcb and stage virtualization
      stage_pcb_class = pcb
      pcb_class = pcb
      if 'stage_virt' in task:
        if task['stage_virt'] == True:
          stage_pcb_class = virt.pcb
      if 'pcb_virt' in task:
        if task['pcb_virt'] == True:
          pcb_class = virt.pcb
      try:  # attempt to do the task
        if task['cmd'] == 'home':
          with stage_pcb_class(task['pcb'], timeout=1) as p:
            mo = motion(address=task['stage_uri'], pcb_object=p)
            mo.connect()
            mo.home()
            self.lg.info('Homing procedure complete.')
            self.send_pos(mo)
          del (mo)

        # send the stage some place
        elif task['cmd'] == 'goto':
          with stage_pcb_class(task['pcb'], timeout=1) as p:
            mo = motion(address=task['stage_uri'], pcb_object=p)
            mo.connect()
            mo.goto(task['pos'])
            self.send_pos(mo)
          del (mo)

        # handle any generic PCB command that has an empty return on success
        elif task['cmd'] == 'for_pcb':
          with pcb_class(task['pcb'], timeout=1) as p:
            # special case for pixel selection to avoid parallel connections
            if (task['pcb_cmd'].startswith('s') and ('stream' not in task['pcb_cmd']) and (len(task['pcb_cmd']) != 1)):
              p.query('s')  # deselect all before selecting one
            result = p.query(task['pcb_cmd'])
          if result == '':
            self.lg.debug(f"Command acknowledged: {task['pcb_cmd']}")
          else:
            self.lg.warn(f"Command {task['pcb_cmd']} not acknowleged with {result}")

        # get the stage location
        elif task['cmd'] == 'read_stage':
          with stage_pcb_class(task['pcb'], timeout=1) as p:
            mo = motion(address=task['stage_uri'], pcb_object=p)
            mo.connect()
            self.send_pos(mo)
          del (mo)

        # zero the mono
        elif task['cmd'] == 'mono_zero':
          if task['mono_virt'] == True:
            self.lg.info("0 GOTO virtually worked!")
            self.lg.info("1 FILTER virtually worked!")
          else:
            with serial.Serial(task['mono_address'], 9600, timeout=1) as mono:
              mono.write("0 GOTO")
              self.lg.info(mono.readline.strip())
              mono.write("1 FILTER")
              self.lg.info(mono.readline.strip())

        elif task['cmd'] == 'spec':
          if task['le_virt'] == True:
            le = virt.illumination(address=task['le_address'], default_recipe=task['le_recipe'])
          else:
            le = illumination(address=task['le_address'], default_recipe=task['le_recipe'], connection_timeout=1)
          con_res = le.connect()
          if con_res == 0:
            response = {}
            int_res = le.set_intensity(task['le_recipe_int'])
            if int_res == 0:
              response["data"] = le.get_spectrum()
              response["timestamp"] = time.time()
              output = {'destination': 'calibration/spectrum', 'payload': pickle.dumps(response)}
              self.outputq.put(output)
            else:
              self.lg.info(f'Unable to set light engine intensity.')
          else:
            self.lg.info(f'Unable to connect to light engine.')
          del (le)

        # device round robin commands
        elif task['cmd'] == 'round_robin':
          if len(task['slots']) > 0:
            with pcb_class(task['pcb'], timeout=1) as p:
              p.query('iv')  # make sure the circuit is in I-V mode (not eqe)
              p.query('s')  # make sure we're starting with nothing selected
              if task['smu_virt'] == True:
                smu = virt.k2400
              else:
                smu = sm
              k = smu(addressString=task['smu'][0]["adress"], terminator=task['smu'][0]["terminator"], serialBaud=task['smu_baud'][0]["baud"], front=task['smu'][0]["front_terminals"])

              # set up sourcemeter for the task
              if task['type'] == 'current':
                pass  # TODO: smu measure current command goes here
              elif task['type'] == 'rtd':
                k.setupDC(auto_ohms=True)
              elif task['type'] == 'connectivity':
                self.lg.info(f'Checking connections. Only failures will be printed.')
                k.set_ccheck_mode(True)

              for i, slot in enumerate(task['slots']):
                dev = task['pads'][i]
                mux_string = task['mux_strings'][i]
                p.query(mux_string)  # select the device
                if task['type'] == 'current':
                  pass  # TODO: smu measure current command goes here
                elif task['type'] == 'rtd':
                  m = k.measure()[0]
                  ohm = m[2]
                  if (ohm < 3000) and (ohm > 500):
                    self.lg.info(f'{slot} -- {dev} Could be a PT1000 RTD at {self.rtd_r_to_t(ohm):.1f} °C')
                elif task['type'] == 'connectivity':
                  if k.contact_check() == False:
                    self.lg.info(f'{slot} -- {dev} appears disconnected.')
                p.query(f"s{slot}0")  # disconnect the slot

              if task['type'] == 'connectivity':
                k.set_ccheck_mode(False)
                self.lg.info('Contact check complete.')
              elif task['type'] == 'rtd':
                self.lg.info('Temperature measurement complete.')
                k.setupDC(sourceVoltage=False)
              p.query("s")
              del (k)
      except Exception as e:
        self.lg.warn(e)
        logging.exception(e)
        try:
          del (le)  # ensure le is cleaned up
        except:
          pass
        try:
          del (mo)  # ensure mo is cleaned up
        except:
          pass
        try:
          del (k)  # ensure k is cleaned up
        except:
          pass

      # system health check
      if task['cmd'] == 'check_health':
        rm = pyvisa.ResourceManager('@py')
        if 'pcb' in task:
          self.lg.info(f"Checking controller@{task['pcb']}...")
          try:
            with pcb_class(task['pcb'], timeout=1) as p:
              self.lg.info('Controller connection initiated')
              self.lg.info(f"Controller firmware version: {p.firmware_version}")
              self.lg.info(f"Controller axes: {p.detected_axes}")
              self.lg.info(f"Controller muxes: {p.detected_muxes}")
          except Exception as e:
            emsg = f'Could not talk to control box'
            self.lg.warn(emsg)
            logging.exception(emsg)

        if 'psu' in task:
          self.lg.info(f"Checking power supply@{task['psu']}...")
          if task['psu_virt'] == True:
            self.lg.info(f'Power supply looks virtually great!')
          else:
            try:
              with rm.open_resource(task['psu']) as psu:
                self.lg.info('Power supply connection initiated')
                idn = psu.query("*IDN?")
                self.lg.info(f'Power supply identification string: {idn.strip()}')
            except Exception as e:
              emsg = f'Could not talk to PSU'
              self.lg.warn(emsg)
              logging.exception(emsg)

        if 'smu' in task:
          for smup in task['smu']:  # loop through the list of SMUs
            self.lg.info(f"Checking sourcemeter@{smup['address']}...")
            if smu['virtual'] == True:
              self.lg.info(f'Sourcemeter looks virtually great!')
            else:
              # for sourcemeter
              open_params = {}
              open_params['resource_name'] = smup['address']
              open_params['timeout'] = 300  # ms
              if 'ASRL' in open_params['resource_name']:  # data bits = 8, parity = none
                open_params['read_termination'] = smup['terminator']  # NOTE: <CR> is "\r" and <LF> is "\n" this is set by the user by interacting with the buttons on the instrument front panel
                open_params['write_termination'] = "\r"  # this is not configuable via the instrument front panel (or in any way I guess)
                open_params['baud_rate'] = smup['baud']  # this is set by the user by interacting with the buttons on the instrument front panel
                open_params['flow_control'] = pyvisa.constants.VI_ASRL_FLOW_RTS_CTS  # user must choose NONE for flow control on the front panel
              elif 'GPIB' in open_params['resource_name']:
                open_params['write_termination'] = "\n"
                open_params['read_termination'] = "\n"
                # GPIB takes care of EOI, so there is no read_termination
                open_params['io_protocol'] = pyvisa.constants.VI_HS488  # this must be set by the user by interacting with the buttons on the instrument front panel by choosing 488.1, not scpi
              elif ('TCPIP' in open_params['resource_name']) and ('SOCKET' in open_params['resource_name']):
                # GPIB <--> Ethernet adapter
                pass

              try:
                with rm.open_resource(**open_params) as smu:
                  self.lg.info('Sourcemeter connection initiated')
                  idn = smu.query("*IDN?")
                  self.lg.info(f'Sourcemeter identification string: {idn}')
              except Exception as e:
                emsg = f'Could not talk to sourcemeter'
                self.lg.warn(emsg)
                logging.exception(emsg)

        if 'lia_address' in task:
          self.lg.info(f"Checking lock-in@{task['lia_address']}...")
          if task['lia_virt'] == True:
            self.lg.info(f'Lock-in looks virtually great!')
          else:
            try:
              with rm.open_resource(task['lia_address'], baud_rate=9600) as lia:
                lia.read_termination = '\r'
                self.lg.info('Lock-in connection initiated')
                idn = lia.query("*IDN?")
                self.lg.info(f'Lock-in identification string: {idn.strip()}')
            except Exception as e:
              emsg = f'Could not talk to lock-in'
              self.lg.warn(emsg)
              logging.exception(emsg)

        if 'mono_address' in task:
          self.lg.info(f"Checking monochromator@{task['mono_address']}...")
          if task['mono_virt'] == True:
            self.lg.info(f'Monochromator looks virtually great!')
          else:
            try:
              with rm.open_resource(task['mono_address'], baud_rate=9600) as mono:
                self.lg.info('Monochromator connection initiated')
                qu = mono.query("?nm")
                self.lg.info(f'Monochromator wavelength query result: {qu.strip()}')
            except Exception as e:
              emsg = f'Could not talk to monochromator'
              self.lg.warn(emsg)
              logging.exception(emsg)

        if 'le_address' in task:
          self.lg.info(f"Checking light engine@{task['le_address']}...")
          le = None
          if task['le_virt'] == True:
            ill = virt.illumination
          else:
            ill = illumination
          try:
            le = ill(address=task['le_address'], default_recipe=task['le_recipe'], connection_timeout=1)
            con_res = le.connect()
            if con_res == 0:
              self.lg.info('Light engine connection successful')
            elif (con_res == -1):
              self.lg.warn("Timeout waiting for wavelabs to connect")
            else:
              self.lg.warn(f"Unable to connect to light engine and activate {task['le_recipe']} with error {con_res}")
          except Exception as e:
            emsg = f'Light engine connection check failed: {e}'
            self.lg.info(emsg)
            logging.exception(emsg)
          try:
            del (le)
          except:
            pass

      self.taskq.task_done()

  # send up a log message to the status channel
  def send_log_msg(self, record):
    payload = {'log': {'level': record.levelno, 'text': record.msg}}
    payload = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    output = {'destination': 'status', 'payload': payload}
    self.outputq.put(output)

  # thread that publishes mqtt messages on behalf of the worker and manager
  def sender(self, mqttc):
    while True:
      to_send = self.outputq.get()
      mqttc.publish(to_send['destination'], to_send['payload'], qos=2).wait_for_publish()
      self.outputq.task_done()

  # thread for the mqtt loop. if that dies, it kills the glib main loop
  def mqtt_loop(self):
    time.sleep(1)
    self.client.loop_forever()
    self.loop.quit()

  # converts RTD resistance to temperature. set r0 to 100 for PT100 and 1000 for PT1000
  def rtd_r_to_t(self, r, r0=1000, poly=None):
    PTCoefficientStandard = collections.namedtuple("PTCoefficientStandard", ["a", "b", "c"])
    # Source: http://www.code10.info/index.php%3Foption%3Dcom_content%26view%3Darticle%26id%3D82:measuring-temperature-platinum-resistance-thermometers%26catid%3D60:temperature%26Itemid%3D83
    ptxIPTS68 = PTCoefficientStandard(+3.90802e-03, -5.80195e-07, -4.27350e-12)
    ptxITS90 = PTCoefficientStandard(+3.9083E-03, -5.7750E-07, -4.1830E-12)
    standard = ptxITS90  # pick an RTD standard

    noCorrection = np.poly1d([])
    pt1000Correction = np.poly1d([1.51892983e-15, -2.85842067e-12, -5.34227299e-09, 1.80282972e-05, -1.61875985e-02, 4.84112370e+00])
    pt100Correction = np.poly1d([1.51892983e-10, -2.85842067e-08, -5.34227299e-06, 1.80282972e-03, -1.61875985e-01, 4.84112370e+00])

    A, B = standard.a, standard.b

    if poly is None:
      if abs(r0 - 1000.0) < 1e-3:
        poly = pt1000Correction
      elif abs(r0 - 100.0) < 1e-3:
        poly = pt100Correction
      else:
        poly = noCorrection

    t = ((-r0 * A + np.sqrt(r0 * r0 * A * A - 4 * r0 * B * (r0 - r))) / (2.0 * r0 * B))

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

    self.client = mqtt.Client()
    self.client.on_connect = self.on_connect
    self.client.on_message = self.handle_message

    # connect to the mqtt server
    self.client.connect(self.mqtt_server_address, port=self.mqtt_server_port, keepalive=60)

    # start the sender (publishes messages from worker and manager)
    threading.Thread(target=self.sender, args=(self.client, ), daemon=True).start()

    # start the mqtt client loop
    threading.Thread(target=self.mqtt_loop).start()

    self.loop.run()  # run the glib loop. gets killed only when client.loop_forever dies


def main():
  parser = argparse.ArgumentParser(description='Utility handler')
  parser.add_argument('-a', '--address', type=str, default='127.0.0.1', const='127.0.0.1', nargs='?', help='ip address/hostname of the mqtt server')
  parser.add_argument('-p', '--port', type=int, default=1883, help="MQTT server port")
  args = parser.parse_args()

  u = UtilityHandler(mqtt_server_address=args.address, mqtt_server_port=args.port)
  u.run()  # loops forever


if __name__ == "__main__":
  main()
