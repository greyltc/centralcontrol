#!/usr/bin/env bash
SCRIPTPATH="$( cd "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"
cd "${SCRIPTPATH}"
./get-deps.sh

# this stuff is needed only for usb gpib under linux
sudo modprobe ni_usb
sudo cp "${SCRIPTPATH}/config/gpib.conf /usr/etc/."
sudo gpib_config
