#!/usr/bin/env python

import logging

def get_args(self):
    """Get CLI arguments and options"""
    parser = argparse.ArgumentParser(description='Automated solar cell IV curve collector using a Keithley 24XX sourcemeter. Data is written to HDF5 files and human readable messages are written to stdout. * denotes arguments that are remembered between calls.')

    parser.add_argument('-v', '--version', action='version', version='%(prog)s ' + central_control.__version__)
    parser.add_argument('-o', '--operator', type=str, required=True, help='Name of operator')
    parser.add_argument('-r', '--run-description', type=str, required=True, help='Words describing the measurements about to be taken')
    parser.add_argument('-p', '--experimental-parameter', type=str, nargs='+', action='append', required=True, help="Space separated experimental parameter name and values. Multiple parameters can be specified by additional uses of '-p'. Use one value per substrate measured. The first item given here is taken to be the parameter name and the rest of the items are taken to be the values for each substrate. eg. '-p Thickness 2m 3m 4m' would attach a Thickness attribute with values 2m 3m and 4m to the first, second and third substrate measured in this run respectively.")

    measure = parser.add_argument_group('optional arguments for measurement configuration')
    measure.add_argument('-d', '--destination', help="*Directory in which to save the output data, '__tmp__' will use a system default temporary directory", type=self.is_dir, action=self.FullPaths)    
    measure.add_argument('-a', "--pixel-address", default=None, type=str, help='Hexadecimal bit mask for enabled pixels, also takes letter-number pixel addresses "0xFC == A1A2A3A4A5A6"')
    measure.add_argument("--sweep", type=self.str2bool, default=True, action=self.RecordPref, const = True, help="*Do an I-V sweep from Voc --> Isc")
    measure.add_argument('--snaith', type=self.str2bool, default=True, action=self.RecordPref, const = True, help="*Do an I-V sweep from Isc --> Voc")
    measure.add_argument('--t-prebias', type=float, action=self.RecordPref, default=10.0, help="*Number of seconds to measure to find steady state Voc and Isc")
    measure.add_argument('--mppt', type=float, action=self.RecordPref, default=37.0, help="*Do maximum power point tracking for this many seconds")
    measure.add_argument('--mppt-params', type=str, action=self.RecordPref, default='basic://7:10', help="*Extra configuration parameters for the maximum power point tracker, see https://git.io/fjfrZ")
    measure.add_argument('-i', '--layout-index', type=int, nargs='*', action=self.RecordPref, default=[], help="*Substrate layout(s) to use for finding pixel areas, read from layouts.ini file in CWD or {:}".format(self.system_layouts_file_fullpath))
    measure.add_argument('--area', type=float, nargs='*', default=[], help="Override pixel areas taken from layout (given in cm^2)")

    setup = parser.add_argument_group('optional arguments for setup configuration')
    setup.add_argument("--ignore-adapter-resistors", type=self.str2bool, default=True, action=self.RecordPref, const = True, help="*Don't consider the resistor value of adapter boards when determining device layouts")
    setup.add_argument("--light-address", type=str, action=self.RecordPref, default='wavelabs-relay://localhost:3335', help="*protocol://hostname:port for communication with the solar simulator, 'none' for no light, 'wavelabs://0.0.0.0:3334' for starting a wavelabs server on port 3334, 'wavelabs-relay://127.0.0.1:3335' for connecting to a wavelabs-relay server")
    setup.add_argument("--motion-address", type=str, action=self.RecordPref, default='none', help="*protocol://hostname:port for communication with the motion controller, 'none' for no motion, 'afms:///dev/ttyAMC0' for an Adafruit Arduino motor shield on /dev/ttyAMC0, 'env://FTDI_DEVICE' to read the address from an environment variable named FTDI_DEVICE")
    setup.add_argument("--rear", type=self.str2bool, default=True, action=self.RecordPref, help="*Use the rear terminals")
    setup.add_argument("--four-wire", type=self.str2bool, default=True, action=self.RecordPref, help="*Use four wire mode (the default)")
    setup.add_argument("--voltage-compliance-override", default=2, type=float, help="Override voltage complaince setting used during Voc measurement")
    setup.add_argument("--current-compliance-override", type=float, help="Override current compliance value used during I-V scans")
    setup.add_argument("--scan-low-override", type=float, help="Override the sweep voltage limit on the Jsc side")
    setup.add_argument("--scan-high-override", type=float, help="Override the scan voltage limit on the Voc side")
    setup.add_argument("--scan-points", type=int, action=self.RecordPref, default = 101, help="*Number of measurement points in I-V curve")
    setup.add_argument("--scan-nplc", type=float, action=self.RecordPref, default = 1, help="*Sourcemeter NPLC setting to use during I-V scans and max power point tracking")  
    setup.add_argument("--sm-terminator", type=str, action=self.RecordPref, default='0A', help="*Visa comms read & write terminator (enter in hex)")
    setup.add_argument("--sm-baud", type=int, action=self.RecordPref, default=57600, help="*Visa serial comms baud rate")
    setup.add_argument("--sm-address", default='GPIB0::24::INSTR', type=str, action=self.RecordPref, help="*VISA resource name for sourcemeter")
    setup.add_argument("--pcb-address", type=str, default='10.42.0.54:23', action=self.RecordPref, help="*host:port for PCB comms")
    setup.add_argument("--calibrate-diodes", default=False, action='store_true', help="Read diode ADC counts now and store those as corresponding to 1.0 sun intensity")    
    setup.add_argument("--diode-calibration-values", type=int, nargs=2, action=self.RecordPref, default=(1,1), help="*Calibration ADC counts for diodes D1 and D2 that correspond to 1.0 sun intensity")
    setup.add_argument('--ignore-diodes', default=False, action='store_true', help="Ignore intensity diode readings and assume 1.0 sun illumination")
    setup.add_argument('--visa-lib', type=str, action=self.RecordPref, default='@py', help="*Path to visa library in case pyvisa can't find it, try C:\\Windows\\system32\\visa64.dll")
    setup.add_argument('--gui-address', type=str, default='http://127.0.0.1:51246', action=self.RecordPref, help='*protocol://host:port for the gui server')
    
    testing = parser.add_argument_group('optional arguments for debugging/testing')
    testing.add_argument('--dummy', default=False, action='store_true', help="Run in dummy mode (doesn't need sourcemeter, generates simulated device data)")
    testing.add_argument("--scan", default=False, action='store_true', help="Scan for obvious VISA resource names, print them and exit")
    testing.add_argument('--test-hardware', default=False, action='store_true', help="Exercises all the hardware, used to check for and debug issues")
    
    args = parser.parse_args()
    
    return args
