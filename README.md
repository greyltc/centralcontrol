# central-control
instrument control server

## Purpose
This software controls laboratory instruments. It receives high level instructions from the user telling it what measurements to make via an MQTT subscription and uses those instructions to decide how to command various instruments and other lab equipment to carry out the measurements the user has requested. It may then publish the measurement data via MQTT.

## Hacking
Make a python virtual environment, `cc_venv`, to hack in:  
```
python3 -m venv cc_venv --system-site-packages
```
`--system-site-packages` might be optional and might be a good idea.

[Read about how to](https://docs.python.org/3/library/venv.html#creating-virtual-environments) activate the virtual environment for your platform. Then activate it. You might find you should do `source cc_venv/bin/activate` or `cc_venv\Scripts\Activate.ps1`.

Install external deps into the virtual envronment
```
python -m pip install -e git+https://github.com/jmball/acton_sp2150.git@master#egg=acton_sp2150
python -m pip install -e git+https://github.com/jmball/srs_sr830.git@master#egg=srs_sr830
python -m pip install -e git+https://github.com/jmball/rigol_dp800.git@master#egg=rigol_dp800
python -m pip install -e git+https://github.com/jmball/eqe.git@master#egg=eqe
python -m pip install -e git+https://github.com/jmball/mqtt_tools.git@master#egg=mqtt_tools
python -m pip install -e git+https://github.com/jmball/mqtt_saver.git@master#egg=mqtt_saver
python -m pip install -e git+https://github.com/jmball/plotter.git@master#egg=plotter
```
Clone this repo: `git clone -b master https://github.com/greyltc/central-control.git` and start hacking (maybe with `cd central-control; code .`).  

When you're done, deactivate the envoronment with `deactivate`