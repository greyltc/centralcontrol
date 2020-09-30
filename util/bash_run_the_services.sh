#!/usr/bin/env bash

# kill the backgrounds on exit
trap "kill 0" EXIT

# make sure cwd is this script
#cd "$(dirname "$0")"

# launching these assume your pythonpath is setup in a special way
python -m plotter.vt_plotter --mqtthost 127.0.0.1 --dashhost 127.0.0.1 &
python -m plotter.it_plotter --mqtthost 127.0.0.1 --dashhost 127.0.0.1 &
python -m plotter.iv_plotter --mqtthost 127.0.0.1 --dashhost 127.0.0.1 &
python -m plotter.eqe_plotter --mqtthost 127.0.0.1 --dashhost 127.0.0.1 &
python -m plotter.mppt_plotter --mqtthost 127.0.0.1 --dashhost 127.0.0.1 &

python -m central_control.utility_handler --address 127.0.0.1 &
python -m central_control_dev.mqtt_server --mqtthost 127.0.0.1 &

mkdir -p /tmp/data
cd /tmp/data
python -m saver.saver -mqtthost 127.0.0.1 &

wait
