# solar-sim-software
Software design files for controlling the hardware from https://github.com/AFMD/solar-sim-electronics

## Exapmle Usage 

```
# scan pixel A4 and save it to a4-light-la.csv
./getCurves.py --file ./a4-light-la.csv ASRL/dev/ttyS0::INSTR 10.42.0.54 A4
```
## Arch Linux deps

```
yay -Syyu --needed python-pyvisa python-pyvisa-py python-pyserial python-h5py
```

## Switch firmware testing
With something like this
```
socat -,rawer,echo,escape=0x03 TCP:10.42.0.54:23
```
