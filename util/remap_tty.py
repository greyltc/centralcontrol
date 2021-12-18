#!/usr/bin/env python3

# usage example:
# sed "$(python /home/labuser/remap_tty.py)" -i /home/labuser/measurement_config.yaml

import serial
import serial.tools.list_ports
import sys
import time

# config sections to rewrite
sections = []
sections.append({"name": "lia", "dev": "/dev/null", "address": "ASRL{0}::INSTR", "match": "Stanfor".encode()})
sections.append({"name": "smu", "dev": "/dev/null", "address": "ASRL{0}::INSTR", "match": "KEITHLE".encode()})
sections.append({"name": "monochromator", "dev": "/dev/null", "address": "ASRL{0}::INSTR", "match": "*IDN? ?".encode()})
config_key_to_change = "address"

comms_rw_timeouts = 1  # s
detection_retries = 3
sleep_after_dc = 10

potential_bauds = [9600, 57600]
potential_devs = [p.device for p in serial.tools.list_ports.comports(include_links=True)]

probe_cmd = "*IDN?"
term = "\r"
matches = [x["match"] for x in sections]

# look through all USB <---> Serial adapters
for dev in potential_devs:
    for baud in potential_bauds:
        print(f"Trying {dev=}, {baud=}")
        response = ""
        match = False
        try:
            for try_num in range(detection_retries):
                with serial.Serial(dev, baud, timeout=comms_rw_timeouts, write_timeout=comms_rw_timeouts) as com:
                    com.reset_input_buffer()
                    com.reset_output_buffer()
                    com.write(f"{probe_cmd}".encode())
                    com.write(f"{term}".encode())
                    com.flush()
                    response = com.read(13)
                    # response = com.readline()
                    print(f"{try_num}:{response=}")
                    if any([x in response for x in matches]):
                        print("match!")
                        # we found a match, update the dict list
                        for i, section in enumerate(sections):
                            if section["match"] in response:
                                sections[i]["dev"] = dev
                        match = True
                        break  # stop checking baud rates and move on to the next port
                    # else:  # match failure. attempt recorvery to unlock instrument
                    #    print("Recovering from nomatch")
                    #    for recovery_baud in potential_bauds:
                    #        if recovery_baud != baud:
                    #            com.baudrate = recovery_baud
                    #            com.write(f"{term}".encode())
                    #            com.flush()
                    com.reset_input_buffer()
                    com.reset_output_buffer()
                time.sleep(sleep_after_dc)
        except Exception as err:
            print(f"Detection error: {dev=}, {baud=}, {response=}", file=sys.stderr)
            print(err, file=sys.stderr)
            # pass
        if match == True:
            break


sed_commands = []
# step through each section of the config file we'd like to rewrite and generate a sed command to change it as needed
for section in sections:
    if "null" in section["dev"]:
        print(f'WARNING: Unable to find connection to {section["name"]}', file=sys.stderr)
    sed_magic = f'/^{section["name"]}:/,/{config_key_to_change}/{{s|{config_key_to_change}:.*|{config_key_to_change} = {section[config_key_to_change].format(section["dev"])}|g}}'
    sed_commands.append(sed_magic)

# unify the sed commands and print it
print(";".join(sed_commands))

# picocom --quiet --exit-after 100 --baud 57600 --initstring "$(echo -ne '*IDN?\r\n')" /dev/ttyUSB1; date
# probe_cmd = "*IDN?\r"
# with serial.Serial("/dev/ttyUSB1", 57600, timeout=10, write_timeout=10) as com:
#     com.reset_input_buffer()
#     com.reset_output_buffer()
#     com.write(f"{probe_cmd}".encode())
#     com.flush()
#     response = com.read(7)
#     print(response)
#     com.reset_input_buffer()
#     com.reset_output_buffer()
