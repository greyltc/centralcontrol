# centralcontrol
instrument control server

## Purpose
This software controls laboratory instruments. It receives high level instructions from the user telling it what measurements to make via an MQTT subscription and uses those instructions to decide how to command various instruments and other lab equipment to carry out the measurements the user has requested. It may then publish the measurement data via MQTT.

## Development workflow
1) Use git to clone this repo and cd into its folder
1) Install dependancies system-wide using your favorite python package manager. View those like this:
    ```bash
    $ hatch project metadata | jq -r '.dependencies | .[]'
    ```
1) Setup a virtual environment for development/testing
    ```bash
    $ python -m venv --without-pip --system-site-packages --clear venv
    ```
1) Activate the venv (this step is os/shell-dependant, see [1] for non-linux/bash)
    ```bash
    $ source venv/bin/activate
    ```
1) Install the package in editable mode into the venv
    ```bash
    (venv) $ python tools/venv_dev_install.py
    ```
1) Develop! When you're finished with it, you can deactivate the virtual environment with `deactivate`