#!/usr/bin/env python3
import paho.mqtt.client as mqtt
import argparse
import pickle
import us
import pcb
import threading, queue
import motion
from collections.abc import Iterable

cmdq = queue.Queue()  # for storing command messages as they arrive
taskq = queue.Queue() # for storing jobs to be worked on

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    print("Connected with result code "+str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    #client.subscribe("$SYS/#")
    client.subscribe("cmd/#")

# The callback for when a PUBLISH message is received from the server.
def handle_message(client, userdata, msg):
    cmdq.put_nowait(msg)  # pass this off for our worker to deal with

    #print(msg.topic+" "+str(msg.payload))


# filters out all mqtt messages except
# properly formatted command messages, unpacks and returns those
# performs
def filter_cmd(mqtt_msg):
    result = {'mcd':''}
    try:
        msg = pickle.loads(mqtt_msg.payload)
    except:
        msg = None
    if isinstance(msg, Iterable):
        if 'cmd' in msg:
            result = msg
    return(result)

# the manager thread decides if the command should be passed on to the worker or rejected.
# immediagely handles estops
def manager():
    while True:
        cmd_msg = filter_cmd(cmdq.get())
        if cmd_msg['cmd'] == 'estop':
                with pcb.pcb(cmd_msg['pcb']) as p:
                    p.get('b')
        elif (taskq.unfinished_tasks == 0):  # the worker is available so let's give it something to do
            taskq.put_nowait(cmd_msg)
        else:
            print('Command rejected.')
        cmdq.task_done()


# work gets done here so that we don't do any processing on the mqtt network thread
def worker():
    while True:
        task = taskq.get()
        if task['cmd'] == 'home':
            with pcb.pcb(task['pcb']) as p:
                mo = motion.motion(address=task['stage_uri'], pcb_object=p)
                mo.connect()
                result = mo.home()
                if isinstance(result, list) or (result == 0):
                    print(f'Home done with result = {result}')
                else:
                    print(f'Home failed with result {result}')
        elif task['cmd'] == 'goto':
            with pcb.pcb(task['pcb']) as p:
                mo = motion.motion(address=task['stage_uri'], pcb_object=p)
                mo.connect()
                result = mo.goto(task['pos'])
                print(f'goto result = {result}')
        taskq.task_done()
        
# a special independant worker that will always be ready to handle an
# emergency stop request
def estop_worker():
    while True:
        msg = filter_cmd(cmdq.get())
        if msg['cmd'] == 'estop':
            with pcb.pcb(msg['pcb']) as p:
                p.get('b')
            cmdq.task_done()


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

    # start the manager
    threading.Thread(target=manager, daemon=True).start()

    # start the worker
    threading.Thread(target=worker, daemon=True).start()

    client.connect(args.address, port=args.port, keepalive=60)

    # Blocking call that processes network traffic, dispatches callbacks and
    # handles reconnecting.
    # Other loop*() functions are available that give a threaded interface and a
    # manual interface.
    client.loop_forever()
