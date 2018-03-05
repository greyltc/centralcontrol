# solar-sim-software
Software design files for controlling the hardware from https://github.com/AFMD/solar-sim-electronics

## Exapmle Usage 

```
# scan pixel A4 and save it to a4-light-la.csv
./getCurves.py --rear --file ./a4-light-la.csv ASRL/dev/ttyS0::INSTR 10.42.0.54 A4
```
## Arch Linux deps

```
pacaur -Syyu python-pyvisa python-pyvisa-py python-pyserial
```
