#!/usr/bin/env python3
import paho.mqtt.client as mqtt
import argparse
import pickle
import us
import pcb
import threading, queue

cmdq = queue.Queue()

# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    print("Connected with result code "+str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    #client.subscribe("$SYS/#")
    client.subscribe("gui/stage")

# The callback for when a PUBLISH message is received from the server.
def handle_message(client, userdata, msg):
    cmdq.put(msg)  # pass this off for our worker to deal with
    #print(msg.topic+" "+str(msg.payload))


# so that we don't do any processing on the mqtt network thread
def worker():
    while True:
        msg = cmdq.get()
        message = pickle.loads(msg.payload)
        if message['cmd'] == 'estop':
            with pcb.pcb(message['pcb']) as p:
                p.get('b')
        if message['cmd'] == 'home':
            with pcb.pcb(message['pcb']) as p:
                md = us.us(p,[250-125])
                md.connect()
                result = md.home(block=False)
                if isinstance(result, list) or (result == 0):
                    print(f'Home done with result = {result}')
                else:
                    print(f'Home failed with result {result}')
        if message['cmd'] == 'goto':
            with pcb.pcb(message['pcb']) as p:
                md = us.us(p,[250-125])
                md.connect()
                md.goto(message['loc'])
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

    # start the worker
    threading.Thread(target=worker, daemon=True).start()

    client.connect(args.address, port=args.port, keepalive=60)

    # Blocking call that processes network traffic, dispatches callbacks and
    # handles reconnecting.
    # Other loop*() functions are available that give a threaded interface and a
    # manual interface.
    client.loop_forever()
