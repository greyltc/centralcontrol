#!/usr/bin/env python3
import paho.mqtt.client as mqtt
import argparse
import pickle
import us
import pcb
import threading, queue
import motion
import logging
from collections.abc import Iterable
import pyvisa

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
    except:
        msg = None
    if isinstance(msg, Iterable):
        if 'cmd' in msg:
            result = msg
    return(result)


# the manager thread decides if the command should be passed on to the worker or rejected.
# immediagely handles estops itself
# this function must be fast and non-blocking to avoid estop service delay
def manager():
    while True:
        cmd_msg = filter_cmd(cmdq.get())
        if cmd_msg['cmd'] == 'estop':
                with pcb.pcb(cmd_msg['pcb']) as p:
                    p.get('b')
                log_msg('Emergency stop done. Re-Homing required before any further movements.',lvl=logging.INFO)
        elif (taskq.unfinished_tasks == 0):
            # the worker is available so let's give it something to do
            taskq.put_nowait(cmd_msg)
        else:
            log_msg('Backend busy. Command rejected.',lvl=logging.WARNING)
        cmdq.task_done()


# work gets done here so that we don't do any processing on the mqtt network thread
# can block and be slow. new commands that come in while this is working will be rejected
def worker():
    while True:
        task = taskq.get()
        if task['cmd'] == 'home':
            with pcb.pcb(task['pcb']) as p:
                mo = motion.motion(address=task['stage_uri'], pcb_object=p)
                mo.connect()
                result = mo.home()
                if isinstance(result, list) or (result == 0):
                    log_msg('Homing procedure complete.',lvl=logging.INFO)
                else:
                    log_msg(f'Home failed with result {result}',lvl=logging.WARNING)

        elif task['cmd'] == 'goto':
            with pcb.pcb(task['pcb']) as p:
                mo = motion.motion(address=task['stage_uri'], pcb_object=p)
                mo.connect()
                result = mo.goto(task['pos'])
                if result != 0:
                    log_msg(f'GOTO failed with result {result}',lvl=logging.WARNING)
        
        elif task['cmd'] == 'check_health':
            log_msg(f'Checking controller...',lvl=logging.INFO)
            with pcb.pcb(task['pcb']) as p:
                log_msg('Controller connected',lvl=logging.INFO)
                log_msg(f"Controller firmware version: {p.get('v')}",lvl=logging.INFO)
                log_msg(f"Controller stage bitmask value: {p.get('e')}",lvl=logging.INFO)
                log_msg(f"Controller mux bitmask value: {p.get('c')}",lvl=logging.INFO)

            log_msg(f'Checking PSU...',lvl=logging.INFO)
            rm = pyvisa.ResourceManager()
            with rm.open_resource(task['psu']) as psu:
                log_msg('PSU connected',lvl=logging.INFO)
                log_msg(f'PSU identification string: {psu.query("*IDN?").strip()}',lvl=logging.INFO)
            
            log_msg(f'Checking SMU...',lvl=logging.INFO)
            with rm.open_resource(task['smu_address'], baud_rate=task['smu_baud'], flow_control=pyvisa.constants.VI_ASRL_FLOW_XON_XOFF) as smu:
                log_msg('SMU connected',lvl=logging.INFO)
                log_msg(f'SMU identification string: {smu.query("*IDN?").strip()}',lvl=logging.INFO)

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


if __name__ == "__main__":
    debug = True
    if debug == False:
        parser = argparse.ArgumentParser(description='Handle gui stage commands')
        parser.add_argument('address', type=str, help='ip address/hostname of the mqtt server')
        parser.add_argument('-p', '--port', type=int, default=1883, help="MQTT server port")

        args = parser.parse_args()
    else:
        class Object(object):
            pass
        args = Object()
        args.address = '127.0.0.1'
        args.port = 1883

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
