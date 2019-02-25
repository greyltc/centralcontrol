# control-software
Software design files for controlling the hardware from https://github.com/mutovis/hardware to perform electrical characterization of research solar cells

## Installation

Follow the instructions given in https://github.com/mutovis/deploy/blob/master/README.md

## Usage
Usage of this program is described by running `mutovis-control-cli --help`: 
```
$ mutovis-control-cli --help
usage: mutovis-control-cli [-h] -o OPERATOR -r RUN_DESCRIPTION -p
                           EXPERIMENTAL_PARAMETER [EXPERIMENTAL_PARAMETER ...]
                           [-d DESTINATION] [-a PIXEL_ADDRESS] [--sweep SWEEP]
                           [--snaith SNAITH] [--t-prebias T_PREBIAS]
                           [--mppt MPPT]
                           [-i [LAYOUT_INDEX [LAYOUT_INDEX ...]]]
                           [--area [AREA [AREA ...]]]
                           [--ignore-adapter-resistors IGNORE_ADAPTER_RESISTORS]
                           [--light-address LIGHT_ADDRESS]
                           [--motion-address MOTION_ADDRESS] [--rear REAR]
                           [--four-wire FOUR_WIRE]
                           [--current-compliance-override CURRENT_COMPLIANCE_OVERRIDE]
                           [--scan-low-override SCAN_LOW_OVERRIDE]
                           [--scan-high-override SCAN_HIGH_OVERRIDE]
                           [--scan-points SCAN_POINTS] [--scan-nplc SCAN_NPLC]
                           [--sm-terminator SM_TERMINATOR] [--sm-baud SM_BAUD]
                           [--sm-address SM_ADDRESS]
                           [--pcb-address PCB_ADDRESS] [--calibrate-diodes]
                           [--diode-calibration-values DIODE_CALIBRATION_VALUES DIODE_CALIBRATION_VALUES]
                           [--ignore-diodes] [--visa-lib VISA_LIB]
                           [--gui-address GUI_ADDRESS] [--dummy] [--scan]
                           [--test-hardware]

Automated solar cell IV curve collector using a Keithley 24XX sourcemeter.
Data is written to HDF5 files and human readable messages are written to
stdout.

optional arguments:
  -h, --help            show this help message and exit
  -o OPERATOR, --operator OPERATOR
                        Name of operator
  -r RUN_DESCRIPTION, --run-description RUN_DESCRIPTION
                        Words describing the measurements about to be taken
  -p EXPERIMENTAL_PARAMETER [EXPERIMENTAL_PARAMETER ...], --experimental-parameter EXPERIMENTAL_PARAMETER [EXPERIMENTAL_PARAMETER ...]
                        Space separated experimental parameter name and
                        values. Multiple parameters can be specified by
                        additional uses of '-p'. Use one value per substrate
                        measured. The first item given here is taken to be the
                        parameter name and the rest of the items are taken to
                        be the values for each substrate. eg. '-p Thickness 2m
                        3m 4m' would attach a Thickness attribute with values
                        2m 3m and 4m to the first, second and third substrate
                        measured in this run respectively.

optional arguments for measurement configuration:
  -d DESTINATION, --destination DESTINATION
                        Directory in which to save the output data, '__tmp__'
                        will use a system default temporary directory
  -a PIXEL_ADDRESS, --pixel-address PIXEL_ADDRESS
                        Hexadecimal bit mask for enabled pixels, also takes
                        letter-number pixel addresses "0xFC == A1A2A3A4A5A6"
  --sweep SWEEP         Do an I-V sweep from Voc --> Isc
  --snaith SNAITH       Do an I-V sweep from Isc --> Voc
  --t-prebias T_PREBIAS
                        Number of seconds to measure to find steady state Voc
                        and Isc
  --mppt MPPT           Do maximum power point tracking for this many seconds
  -i [LAYOUT_INDEX [LAYOUT_INDEX ...]], --layout-index [LAYOUT_INDEX [LAYOUT_INDEX ...]]
                        Substrate layout(s) to use for finding pixel areas,
                        read from layouts.ini file in CWD or
                        /usr/etc/layouts.ini
  --area [AREA [AREA ...]]
                        Override pixel areas taken from layout (given in cm^2)

optional arguments for setup configuration:
  --ignore-adapter-resistors IGNORE_ADAPTER_RESISTORS
                        Don't consider the resistor value of adapter boards
                        when determining device layouts
  --light-address LIGHT_ADDRESS
                        protocol://hostname:port for communication with the
                        solar simulator, 'none' for no light,
                        'wavelabs://0:3334' for starting a wavelabs server on
                        port 3334
  --motion-address MOTION_ADDRESS
                        protocol://hostname:port for communication with the
                        motion controller, 'none' for no motion,
                        'afms:///dev/ttyAMC0' for an Adafruit Arduino motor
                        shield on /dev/ttyAMC0
  --rear REAR           Use the rear terminals
  --four-wire FOUR_WIRE
                        Use four wire mode (the default)
  --current-compliance-override CURRENT_COMPLIANCE_OVERRIDE
                        Override current compliance value used during I-V
                        scans
  --scan-low-override SCAN_LOW_OVERRIDE
                        Override more negative scan voltage value
  --scan-high-override SCAN_HIGH_OVERRIDE
                        Override more positive scan voltage value
  --scan-points SCAN_POINTS
                        Number of measurement points in I-V curve
  --scan-nplc SCAN_NPLC
                        Sourcemeter NPLC setting to use during I-V scans and
                        max power point tracking
  --sm-terminator SM_TERMINATOR
                        Visa comms read & write terminator (enter in hex)
  --sm-baud SM_BAUD     Visa serial comms baud rate
  --sm-address SM_ADDRESS
                        VISA resource name for sourcemeter
  --pcb-address PCB_ADDRESS
                        host:port for PCB comms
  --calibrate-diodes    Read diode ADC counts now and store those as
                        corresponding to 1.0 sun intensity
  --diode-calibration-values DIODE_CALIBRATION_VALUES DIODE_CALIBRATION_VALUES
                        Calibration ADC counts for diodes D1 and D2 that
                        correspond to 1.0 sun intensity
  --ignore-diodes       Ignore intensity diode readings and assume 1.0 sun
                        illumination
  --visa-lib VISA_LIB   Path to visa library in case pyvisa can't find it, try
                        C:\Windows\system32\visa64.dll
  --gui-address GUI_ADDRESS
                        protocol://host:port for the gui server

optional arguments for debugging/testing:
  --dummy               Run in dummy mode (doesn't need sourcemeter, generates
                        simulated device data)
  --scan                Scan for obvious VISA resource names, print them and
                        exit
  --test-hardware       Exercises all the hardware, used to check for and
                        debug issues
```

## Example Usage
```
./mutovis-control-cli --destination /home/labuser/data --pcb-address 10.42.0.54:23 --sm-address GPIB0::24::INSTR --rear false --four-wire on --light-address wavelabs://0.0.0.0:3334 --motion-address afms:///dev/ttyAMC0 --pixel-address A4B1 --sweep on --snaith on --mppt 37  -o labuser -r "buffalo thickness study" -p thickness 1.2m 2.4m -p "hair color" turquoise blond
```
These options tell the program to take a series (or "run") of I-V measurements on various pixels and
- save output data in the `/home/labuser/data` folder
- communicate with the pixel switching system with ip address 10.42.0.54
- measure with a keithley sourcemeter on gpib adapter 0 with address 24
- via the sourcemeter's front terminals
- using 4 wire measurements
- control a wavelabs light source which is configured for remote control by calling this host on port 3334
- control an arduino with an adafruit motor shield connected to the /dev/ttyACM0 serial port to position the pixels under the light
- measure pixel 4 on substrate A and pixel 1 on substrate B
- measure steady state Voc for 10 seconds
- sweep from Voc --> Isc
- measure steady state Isc for 10 seconds
- then sweep from Isc --> Voc
- then track the maximum power point for 37 seconds
- record the user name "labuser" into the run data file
- record a run description of "buffalo thickness study" into the run data file
- record two experimental variables, thickness and hair color, where thickness for the devices of substrate A is recorded into the run data file as 1.2m and B is 2.4m and where hair color for substrate A is turquoise and substrate B is blond

## Hacking this
```bash
git clone https://github.com/mutovis/control-software
cd control-software
# do your hacking here
python3 mutovis-control-cli
```
