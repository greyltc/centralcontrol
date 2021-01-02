#!/usr/bin/env python3

# this boilerplate allows this module to be run directly as a script
if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    __package__ = "centralcontrol"
    from pathlib import Path
    import sys
    # get the dir that holds __package__ on the front of the search path
    print(__file__)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import paho.mqtt.client as mqtt
import argparse
import pickle
import threading
import queue
import serial  # for mono

from . import virt
from .motion import motion
from .k2400 import k2400 as sm
from .illumination import illumination
from .pcb import pcb
import logging
import pyvisa
import collections
import numpy as np
import time

# for storing command messages as they arrive
cmdq = queue.Queue()

# for storing jobs to be worked on
taskq = queue.Queue()

# for outgoing messages
outputq = queue.Queue()

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    print("Connected with result code "+str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe("cmd/#", qos=2)


# The callback for when a PUBLISH message is received from the server.
# this function must be fast and non-blocking to avoid estop service delay
def handle_message(client, userdata, msg):
    cmdq.put_nowait(msg)  # pass this off for our worker to deal with


# filters out all mqtt messages except
# properly formatted command messages, unpacks and returns those
# this function must be fast and non-blocking to avoid estop service delay
def filter_cmd(mqtt_msg):
    result = {'cmd':''}
    try:
        msg = pickle.loads(mqtt_msg.payload)
    except Exception:
        msg = None
    if isinstance(msg, collections.abc.Iterable):
        if 'cmd' in msg:
            result = msg
    return(result)


# the manager thread decides if the command should be passed on to the worker or rejected.
# immediagely handles estops itself
# this function must be fast and non-blocking to avoid estop service delay
def manager():
    while True:
        cmd_msg = filter_cmd(cmdq.get())
        log_msg('New command message!',lvl=logging.DEBUG)
        if cmd_msg['cmd'] == 'estop':
            if cmd_msg['pcb_virt'] == True:
                tpcb = virt.pcb
            else:
                tpcb = pcb
            try:
                with tpcb(cmd_msg['pcb'], timeout=10) as p:
                    p.query('b')
                log_msg('Emergency stop command issued. Re-Homing required before any further movements.', lvl=logging.INFO)
            except Exception as e:
                emsg = "Unable to emergency stop."
                log_msg(emsg, lvl=logging.WARNING)
                logging.exception(emsg)
        elif (taskq.unfinished_tasks == 0):
            # the worker is available so let's give it something to do
            taskq.put_nowait(cmd_msg)
        elif (taskq.unfinished_tasks > 0):
            log_msg(f'Backend busy (task queue size = {taskq.unfinished_tasks}). Command rejected.', lvl=logging.WARNING)
        else:
            log_msg(f'Command message rejected:: {cmd_msg}', lvl=logging.DEBUG)
        cmdq.task_done()

# asks for the current stage position and sends it up to /response
def send_pos(mo):
    pos = mo.get_position()
    payload = {'pos': pos}
    payload = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    output = {'destination':'response', 'payload': payload}  # post the position to the response channel
    outputq.put(output)

# work gets done here so that we don't do any processing on the mqtt network thread
# can block and be slow. new commands that come in while this is working will be rejected
def worker():
    while True:
        task = taskq.get()
        log_msg(f"New task: {task['cmd']} (queue size = {taskq.unfinished_tasks})",lvl=logging.DEBUG)
        # handle pcb and stage virtualization
        stage_pcb_class = pcb
        pcb_class = pcb
        if 'stage_virt' in task:
            if task['stage_virt'] == True:
                stage_pcb_class = virt.pcb
        if 'pcb_virt' in task:
            if task['pcb_virt'] == True:
                pcb_class = virt.pcb
        try:
            if task['cmd'] == 'home':
                with stage_pcb_class(task['pcb'], timeout=1) as p:
                    mo = motion(address=task['stage_uri'], pcb_object=p)
                    mo.connect()
                    mo.home()
                    log_msg('Homing procedure complete.',lvl=logging.INFO)
                    send_pos(mo)

            # send the stage some place
            elif task['cmd'] == 'goto':
                with stage_pcb_class(task['pcb'], timeout=1) as p:
                    mo = motion(address=task['stage_uri'], pcb_object=p)
                    mo.connect()
                    mo.goto(task['pos'])
                    send_pos(mo)

            # handle any generic PCB command that has an empty return on success
            elif task['cmd'] == 'for_pcb':
                with pcb_class(task['pcb'], timeout=1) as p:
                    # special case for pixel selection to avoid parallel connections
                    if (task['pcb_cmd'].startswith('s') and ('stream' not in task['pcb_cmd']) and (len(task['pcb_cmd']) != 1)):
                        p.query('s')  # deselect all before selecting one
                    result = p.query(task['pcb_cmd'])
                if result == '':
                    log_msg(f"Command acknowledged: {task['pcb_cmd']}", lvl=logging.DEBUG)
                else:
                    log_msg(f"Command {task['pcb_cmd']} not acknowleged with {result}", lvl=logging.WARNING)

            # get the stage location
            elif task['cmd'] == 'read_stage':
                with stage_pcb_class(task['pcb'], timeout=1) as p:
                    mo = motion(address=task['stage_uri'], pcb_object=p)
                    mo.connect()
                    send_pos(mo)

            # zero the mono
            elif task['cmd'] == 'mono_zero':
                if task['mono_virt'] == True:
                    log_msg("0 GOTO virtually worked!", lvl=logging.INFO)
                    log_msg("1 FILTER virtually worked!", lvl=logging.INFO)
                else:

                    with serial.Serial(task['mono_address'], 9600, timeout=1) as mono:
                        mono.write("0 GOTO")
                        log_msg(mono.readline.strip(), lvl=logging.INFO)
                        mono.write("1 FILTER")
                        log_msg(mono.readline.strip(), lvl=logging.INFO)


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
                        output = {'destination':'calibration/spectrum', 'payload': pickle.dumps(response)}
                        outputq.put(output)
                    else:
                        log_msg(f'Unable to set light engine intensity.',lvl=logging.INFO)
                else:
                    log_msg(f'Unable to connect to light engine.',lvl=logging.INFO)
                le.disconnect()

            # device round robin commands
            elif task['cmd'] == 'round_robin':
                if len(task['slots']) > 0:
                    with pcb_class(task['pcb'], timeout=1) as p:
                        p.query('iv') # make sure the circuit is in I-V mode (not eqe)
                        p.query('s') # make sure we're starting with nothing selected
                        if task['smu_virt'] == True:
                            smu = virt.k2400
                        else:
                            smu = sm
                        k = smu(addressString=task['smu_address'], terminator=task['smu_le'], serialBaud=task['smu_baud'], front=False)

                        # set up sourcemeter for the task
                        if task['type'] == 'current':
                            pass  # TODO: smu measure current command goes here
                        elif task['type'] == 'rtd':
                            k.setupDC(auto_ohms=True)
                        elif task['type'] == 'connectivity':
                            log_msg(f'Checking connections. Only failures will be printed.',lvl=logging.INFO)
                            k.set_ccheck_mode(True)

                        for i, slot in enumerate(task['slots']):
                            dev = task['pads'][i]
                            p.query(f"s{slot}{dev}")  # select the device
                            if task['type'] == 'current':
                                pass  # TODO: smu measure current command goes here
                            elif task['type'] == 'rtd':
                                m = k.measure()[0]
                                ohm = m[2]
                                if (ohm < 3000) and (ohm > 500):
                                    log_msg(f'{slot} -- {dev} Could be a PT1000 RTD at {rtd_r_to_t(ohm):.1f} °C',lvl=logging.INFO)
                            elif task['type'] == 'connectivity':
                                if k.contact_check() == False:
                                    log_msg(f'{slot} -- {dev} appears disconnected.',lvl=logging.INFO)
                            p.query(f"s{slot}0") # disconnect the slot

                        if task['type'] == 'connectivity':
                            k.set_ccheck_mode(False)
                            log_msg(f'Contact check complete.',lvl=logging.INFO)
                        elif task['type'] == 'rtd':
                            log_msg(f'Temperature measurement complete.',lvl=logging.INFO)
                            k.setupDC(sourceVoltage=False)
                        p.query("s")
                        k.disconnect()
        except Exception as e:
            log_msg(e, lvl=logging.WARNING)
            logging.exception(e)

        # system health check
        if task['cmd'] == 'check_health':
            rm = pyvisa.ResourceManager('@py')
            if 'pcb' in task:
                log_msg(f"Checking controller@{task['pcb']}...",lvl=logging.INFO)
                try:
                    with pcb_class(task['pcb'], timeout=1) as p:
                        log_msg('Controller connection initiated',lvl=logging.INFO)
                        log_msg(f"Controller firmware version: {p.firmware_version}",lvl=logging.INFO)
                        log_msg(f"Controller axes: {p.detected_axes}",lvl=logging.INFO)
                        log_msg(f"Controller muxes: {p.detected_muxes}",lvl=logging.INFO)
                except Exception as e:
                    emsg = f'Could not talk to control box'
                    log_msg(emsg, lvl=logging.WARNING)
                    logging.exception(emsg)

            if 'psu' in task:
                log_msg(f"Checking power supply@{task['psu']}...",lvl=logging.INFO)
                if task['psu_virt'] == True:
                    log_msg(f'Power supply looks virtually great!',lvl=logging.INFO)
                else:
                    try:
                        with rm.open_resource(task['psu']) as psu:
                            log_msg('Power supply connection initiated',lvl=logging.INFO)
                            idn = psu.query("*IDN?")
                            log_msg(f'Power supply identification string: {idn.strip()}',lvl=logging.INFO)
                    except Exception as e:
                        emsg = f'Could not talk to PSU'
                        log_msg(emsg, lvl=logging.WARNING)
                        logging.exception(emsg)

            if 'smu_address' in task:
                log_msg(f"Checking sourcemeter@{task['smu_address']}...",lvl=logging.INFO)
                if task['smu_virt'] == True:
                    log_msg(f'Sourcemeter looks virtually great!',lvl=logging.INFO)
                else:
                    # for sourcemeter
                    open_params = {}
                    open_params['resource_name'] = task['smu_address']
                    open_params['timeout'] = 300 # ms
                    if 'ASRL' in open_params['resource_name']:  # data bits = 8, parity = none
                        open_params['read_termination'] = task['smu_le']  # NOTE: <CR> is "\r" and <LF> is "\n" this is set by the user by interacting with the buttons on the instrument front panel
                        open_params['write_termination'] = "\r" # this is not configuable via the instrument front panel (or in any way I guess)
                        open_params['baud_rate'] = task['smu_baud']  # this is set by the user by interacting with the buttons on the instrument front panel
                        open_params['flow_control'] = pyvisa.constants.VI_ASRL_FLOW_RTS_CTS # user must choose NONE for flow control on the front panel
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
                            log_msg('Sourcemeter connection initiated',lvl=logging.INFO)
                            idn = smu.query("*IDN?")
                            log_msg(f'Sourcemeter identification string: {idn}',lvl=logging.INFO)
                    except Exception as e:
                        emsg = f'Could not talk to sourcemeter'
                        log_msg(emsg, lvl=logging.WARNING)
                        logging.exception(emsg)

            if 'lia_address' in task:
                log_msg(f"Checking lock-in@{task['lia_address']}...",lvl=logging.INFO)
                if task['lia_virt'] == True:
                    log_msg(f'Lock-in looks virtually great!',lvl=logging.INFO)
                else:
                    try:
                        with rm.open_resource(task['lia_address'], baud_rate=9600) as lia:
                            lia.read_termination = '\r'
                            log_msg('Lock-in connection initiated',lvl=logging.INFO)
                            idn = lia.query("*IDN?")
                            log_msg(f'Lock-in identification string: {idn.strip()}',lvl=logging.INFO)
                    except Exception as e:
                        emsg = f'Could not talk to lock-in'
                        log_msg(emsg, lvl=logging.WARNING)
                        logging.exception(emsg)

            if 'mono_address' in task:
                log_msg(f"Checking monochromator@{task['mono_address']}...",lvl=logging.INFO)
                if task['mono_virt'] == True:
                    log_msg(f'Monochromator looks virtually great!',lvl=logging.INFO)
                else:
                    try:
                        with rm.open_resource(task['mono_address'], baud_rate=9600) as mono:
                            log_msg('Monochromator connection initiated',lvl=logging.INFO)
                            qu = mono.query("?nm")
                            log_msg(f'Monochromator wavelength query result: {qu.strip()}',lvl=logging.INFO)
                    except Exception as e:
                        emsg = f'Could not talk to monochromator'
                        log_msg(emsg, lvl=logging.WARNING)
                        logging.exception(emsg)

            if 'le_address' in task:
                log_msg(f"Checking light engine@{task['le_address']}...", lvl=logging.INFO)
                if task['le_virt'] == True:
                    ill = virt.illumination
                else:
                    ill = illumination
                try:
                    le = ill(address=task['le_address'], default_recipe=task['le_recipe'], connection_timeout=1)
                    con_res = le.connect()
                    le.disconnect()
                    if con_res == 0:
                        log_msg('Light engine connection successful',lvl=logging.INFO)
                    elif (con_res == -1):
                        log_msg("Timeout waiting for wavelabs to connect",lvl=logging.WARNING)
                    else:
                        log_msg(f"Unable to connect to light engine and activate {task['le_recipe']} with error {con_res}",lvl=logging.WARNING)
                except Exception as e:
                    emsg = f'Could not talk to light engine'
                    log_msg(emsg,lvl=logging.WARNING)
                    logging.exception(emsg)

        taskq.task_done()


# send up a log message to the status channel
def log_msg(msg, lvl=logging.DEBUG):
    payload = {'log':{'level':lvl, 'text':msg}}
    payload = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    output = {'destination':'status', 'payload': payload}
    outputq.put(output)


# thread that publishes mqtt messages on behalf of the worker and manager
def sender(mqttc):
    while True:
        to_send = outputq.get()
        mqttc.publish(to_send['destination'], to_send['payload'], qos=2).wait_for_publish()
        outputq.task_done()

# converts RTD resistance to temperature. set r0 to 100 for PT100 and 1000 for PT1000
def rtd_r_to_t(r, r0=1000, poly=None):
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

def main():
    parser = argparse.ArgumentParser(description='Handle gui stage commands')
    parser.add_argument('-a', '--address', type=str, default='127.0.0.1', help='ip address/hostname of the mqtt server')
    parser.add_argument('-p', '--port', type=int, default=1883, help="MQTT server port")
    args = parser.parse_args()

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = handle_message

    # start the manager (decides what to do with commands from mqtt)
    threading.Thread(target=manager, daemon=True).start()

    # start the worker (does tasks the manger tells it to)
    threading.Thread(target=worker, daemon=True).start()

    # connect to the mqtt server
    client.connect(args.address, port=args.port, keepalive=60)

    # start the sender (publishes messages from worker and manager)
    threading.Thread(target=sender, args=(client,)).start()

    # Blocking call that processes network traffic, dispatches callbacks and
    # handles reconnecting.
    # Other loop*() functions are available that give a threaded interface and a
    # manual interface.
    client.loop_forever()

if __name__ == "__main__":
    main()
