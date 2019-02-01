# control-software
Software design files for controlling the hardware from https://github.com/mutovis/hardware

## Installation

Follow the instructions given in https://github.com/mutovis/deploy/blob/master/README.md

## Usage
Usage of this program is described by running `cli.py --help`: 
```
$ ./cli.py --help
usage: cli.py [-h] [--destination DESTINATION] [--pixel_address PIXEL_ADDRESS]
              [--sweep SWEEP] [--snaith SNAITH] [--t-prebias T_PREBIAS]
              [--area AREA] [--mppt MPPT] [--t-dwell T_DWELL]
              [--relay-ip RELAY_IP] [--wavelabs WAVELABS] [--rear REAR]
              [--four-wire FOUR_WIRE]
              [--current-compliance-override CURRENT_COMPLIANCE_OVERRIDE]
              [--scan-low-override SCAN_LOW_OVERRIDE]
              [--scan-high-override SCAN_HIGH_OVERRIDE]
              [--scan-points SCAN_POINTS] [--scan-nplc SCAN_NPLC]
              [--terminator TERMINATOR] [--baud BAUD] [--port PORT]
              [--address ADDRESS] [--switch-address SWITCH_ADDRESS]
              [--diode-calibration DIODE_CALIBRATION DIODE_CALIBRATION]
              [--ignore-diodes] [--visa-lib VISA_LIB] [--dummy] [--scan]
              [--test-hardware]
              operator

Automated solar cell IV curve collector using a Keithley 24XX sourcemeter.
Data is written to HDF5 files and human readable messages are written to
stderr.

positional arguments:
  operator              Name of operator

optional arguments:
  -h, --help            show this help message and exit
  --destination DESTINATION
                        Save output files here. '__tmp__' will use a system
                        default temporary directory

optional arguments for measurement configuration:
  --pixel_address PIXEL_ADDRESS
                        Hex value to specify an enabled pixel bitmask (must
                        start with 0x...)
  --sweep SWEEP         Do an I-V sweep from Voc --> Jsc
  --snaith SNAITH       Do an I-V sweep from Jsc --> Voc
  --t-prebias T_PREBIAS
                        Number of seconds to sit at initial voltage value
                        before doing sweep
  --area AREA           Specify device area in cm^2
  --mppt MPPT           Do maximum power point tracking for this many cycles
  --t-dwell T_DWELL     Total number of seconds for the dwell mppt phase(s)

optional arguments for setup configuration:
  --relay-ip RELAY_IP   IP address of the WaveLabs relay server (set to 0 for
                        direct WaveLabs connection)
  --wavelabs WAVELABS   WaveLabs LED solar sim is present
  --rear REAR           Use the rear terminals
  --four-wire FOUR_WIRE
                        Use four wire mode (the defalt)
  --current-compliance-override CURRENT_COMPLIANCE_OVERRIDE
                        Override current compliance value used diring I-V
                        scans
  --scan-low-override SCAN_LOW_OVERRIDE
                        Override more negative scan voltage value
  --scan-high-override SCAN_HIGH_OVERRIDE
                        Override more positive scan voltage value
  --scan-points SCAN_POINTS
                        Number of measurement points in I-V curve
  --scan-nplc SCAN_NPLC
                        Sourcemeter NPLC setting to use during I-V scan
  --terminator TERMINATOR
                        Instrument comms read & write terminator (enter in
                        hex)
  --baud BAUD           Instrument serial comms baud rate
  --port PORT           Port to connect to switch hardware
  --address ADDRESS     VISA resource name for sourcemeter
  --switch-address SWITCH_ADDRESS
                        IP address for PCB
  --diode-calibration DIODE_CALIBRATION DIODE_CALIBRATION
                        Calibration ADC counts for diodes D1 and D2 that
                        correspond to 1 sun
  --ignore-diodes       Assume 1.0 sun illumination
  --visa-lib VISA_LIB   Path to visa library in case pyvisa can't find it, try
                        C:\Windows\system32\visa64.dll

optional arguments for debugging/testing:
  --dummy               Run in dummy mode (doesn't need sourcemeter, generates
                        simulated device data)
  --scan                Scan for obvious VISA resource names, print them and
                        exit
  --test-hardware       Exercises all the hardware, used to check for and
                        debug issues
```

## Exapmle Usage
```
# measure pixels 1 and 2 on substrate A
# collect forward and reverse I-V sweeps with steady state measurements for Voc and Isc)
# as a user called "labuser"
# saving the data into the folder /home/labuser/data
./cli.py --pixel_address 0xC0 --address GPIB0::24::INSTR --switch_address=10.42.0.54  --sweep true --snaith true --t_prebias 10.0 --wavelabs false --rear true --four-wire true --ignore-diodes false --area 0.1824 --destination /home/labuser/data labuser
```
