# centralcontrol
instrument control server

## Purpose
This software controls laboratory instruments. It receives high level instructions from the user telling it what measurements to make via an MQTT subscription and uses those instructions to decide how to command various instruments and other lab equipment to carry out the measurements the user has requested. It may then publish the measurement data via MQTT.

## Hacking
First, __if__ you're in conda, setup a new conda virual environment and install some base packages with conda like so:
```
conda create -n ccc_venv python gtk3 pygobject numpy
conda activate ccc_venv
```
Make a python virtual environment, `cc_venv`, to hack in:  
```
python -m venv cc_venv --system-site-packages
```
`--system-site-packages` _might_ be optional and _might_ be a good idea.

[Read about how to](https://docs.python.org/3/library/venv.html#creating-virtual-environments) activate the virtual environment for your platform. Then activate it. You might find you should do `source cc_venv/bin/activate` or `cc_venv\Scripts\Activate.ps1`.

Now install stuff into the virtual envronment with pip like so
```
python -m pip install wheel
python -m pip install -e git+https://github.com/jmball/acton_sp2150.git@master#egg=acton_sp2150
python -m pip install -e git+https://github.com/jmball/srs_sr830.git@master#egg=srs_sr830
python -m pip install -e git+https://github.com/jmball/rigol_dp800.git@master#egg=rigol_dp800
python -m pip install -e git+https://github.com/jmball/mqtt_tools.git@master#egg=mqtt_tools
python -m pip install -e git+https://github.com/jmball/eqe.git@master#egg=eqe
python -m pip install -e git+https://github.com/greyltc/centralcontrol.git@master#egg=centralcontrol
# TODO: there's a circular dependency between centralcontrol and eqe. need to test that for install time issues
python -m pip install -e git+https://github.com/jmball/plotter.git@master#egg=plotter
python -m pip install -e git+https://github.com/jmball/mqtt_saver.git@master#egg=mqtt_saver
python -m pip install -e git+https://gitlab.com/greyltc/runpanel.git@master#egg=runpanel
```

When you're done, deactivate the environment with `deactivate`
