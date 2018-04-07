#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# written by grey@christoforo.net

from toolkit import k2400
from toolkit import pcb
from toolkit import devsim
import sys
import argparse
import time
import numpy
import mpmath
from scipy import special
parser = argparse.ArgumentParser(description='Automated solar cell IV curve collector using a Keithley 2400 sourcemeter. Data is written to stdout and human readable messages are written to stderr.')

parser.add_argument("address", nargs='?', default="None", type=str, help="VISA resource name for sourcemeter")
parser.add_argument("switch_address", nargs='?', default=None, type=str,\
                    help="IP address for switch hardware")
parser.add_argument("pixel_address", nargs='?', default=None, type=str, help="Pixel to scan (A1, A2,...)")
#parser.add_argument("t_dwell", nargs='?', default=None,  type=int, help="Total number of seconds for the dwell phase(s)")
parser.add_argument('--dummy', default=False, action='store_true', help="Run in dummy mode (doesn't need sourcemeter, generates simulated device data)")
parser.add_argument('--visa_lib', type=str, default='@py', help="Path to visa library in case pyvisa can't find it, try C:\\Windows\\system32\\visa64.dll")
parser.add_argument('--file', type=str, help="Write output data stream to this file in addition to stdout.")
parser.add_argument("--scan", default=False, action='store_true', help="Scan for obvious VISA resource names, print them and exit")
parser.add_argument("--front", default=False, action='store_true', help="Use the front terminals")
parser.add_argument("--two-wire", default=False, dest='twoWire', action='store_true', help="Use two wire mode")
parser.add_argument("--terminator", type=str, default='0A', help="Instrument comms read & write terminator (enter in hex)")
parser.add_argument("--baud", type=int, default=57600, help="Instrument serial comms baud rate")
parser.add_argument("--port", type=int, default=23, help="Port to connect to switch hardware")
parser.add_argument('--xmas-lights', default=False, action='store_true', help="Connectivity test. Probs only run this with commercial LEDs.")
parser.add_argument('--snaith', default=False, action='store_true', help="Run the IV scan from Isc --> Voc")
parser.add_argument('--area', type=float, default=1.0, help="Specify device area in cm^2")
parser.add_argument('--mppt', type=float, default=0, help="Do maximum power point tracking for this many seconds")

args = parser.parse_args()

args.terminator = bytearray.fromhex(args.terminator).decode()

dataDestinations = [sys.stdout]

if args.dummy:
  sm = devsim()
else:
  sm = k2400(visa_lib=args.visa_lib, terminator=args.terminator, addressString=args.address, serialBaud=args.baud, scan=args.scan)

if not sm.readyForAction:
  raise ValueError('Sourcemeter not ready for action :-(')  

pcb = pcb(args.switch_address, port=args.port)

def myPrint(*args,**kwargs):
    if kwargs.__contains__('file'):
        print(*args,**kwargs) # if we specify a file dest, don't overwrite it
    else:# if we were writing to stdout, also write to the other destinations
        for dest in dataDestinations:
            kwargs['file'] = dest
            print(*args,**kwargs)

if args.file is not None:
    f = open(args.file, 'w')
    dataDestinations.append(f)

if args.xmas_lights:
    substrate = args.pixel_address[0] 
    myPrint("LED test mode active on substrate {:s}".format(substrate), file=sys.stderr, flush=True)
    
    sweepHigh = 0.01 # amps
    sweepLow = 0 # amps    
    
    sweepParams = {} # here we'll store the parameters that define our sweep
    sweepParams['voltage'] = False # sweep in current
    sweepParams['compliance'] = 2.5 # volts
    sweepParams['nPoints'] = 101
    sweepParams['stepDelay'] = -1 # seconds (-1 for auto, nearly zero, delay)
    sweepParams['nplc'] = 0.01
    sweepParams['sweepStart'] = sweepLow
    sweepParams['sweepEnd'] = sweepHigh
    
    if sweepParams['voltage']:
        sweepee = 'voltage'
    else:
        sweepee = 'current'
    
    pcb.pix_picker(substrate,1)
    sm.setupSweep(sweepParams)
    
    substrate = args.pixel_address[0]
    for pix in range(8):
        pcb.pix_picker(substrate,pix+1)
        
        sm.updateSweepStart(sweepLow)
        sm.updateSweepStop(sweepHigh)
        sm.write(':init')
        sm.query_values(':sense:data:latest?')
        #sm.query_values('FETCH?')
        
        sm.updateSweepStart(sweepHigh)
        sm.updateSweepStop(sweepLow)
        sm.write(':init')
        sm.query_values(':sense:data:latest?')
        
        # off during pix switchover
        sm.setOutput(0)
    
    sm.outOn(False)
    
    # deselect all pixels
    pcb.pix_picker(substrate, 0)
    
else: # not running in LED test mode
    substrate = args.pixel_address[0] 
    pix = args.pixel_address[1] 
    # let's find our open circuit voltage
    sm.write(':source:function current')
    sm.write(':source:current:mode fixed')
    sm.write(':source:current:range min')
    sm.write(':source:current 0')
    sm.write(':sense:voltage:protection 2')
    sm.write(':sense:voltage:range 2')
    
    sm.write(':sense:voltage:nplcycles 10')
    sm.write(':sense:current:nplcycles 10')
    sm.write(':display:digits 7')
    

    if not pcb.pix_picker(substrate, pix):
        myPrint("ERROR: Pixel selection failure.", file=sys.stderr, flush=True)
        sys.exit(-1)
    
    sm.write(':output on')
    exploring = 1
    myPrint("Measuring Voc...", file=sys.stderr, flush=True)
    [Voc, Ioc, t0, status] = sm.query_values('READ?')
    myPrint(Voc, file=sys.stderr, flush=True)
    
    vOC_measure_time = 10; #[s]
    t = 0
    while t < vOC_measure_time:
        # read OCV
        [Voc, Ioc, now, status] = sm.query_values('READ?')
        myPrint(Voc, file=sys.stderr, flush=True)
        t = now - t0
    
    #sm.write(':output off')
    myPrint('#exploring,time,voltage,current', file=sys.stderr, flush=True)
    
    # derive connection polarity from Voc
    if Voc < 0:
        polarity = -1
    else:
        polarity = 1
    
    myPrint('# i-v file format v1', flush=True)
    myPrint('# Area = {:}'.format(args.area))
    myPrint('# exploring,time,voltage,current', flush=True)
    myPrint('{:1d},{:.4e},{:.4e},{:.4e}'.format(exploring,0,Voc*polarity,Ioc*polarity), flush=True)
    
    # for initial sweep
    ##NOTE: what if Isc degrades the device? maybe I should only sweep backwards
    ##until the power output starts dropping instead of going all the way to zero volts...
    sweepParams = {} # here we'll store the parameters that define our sweep
    sweepParams['maxCurrent'] = 0.04 # amps
    sweepParams['sweepStart'] = Voc # volts
    sweepParams['sweepEnd'] = 0 # volts
    sweepParams['nPoints'] = 1001
    sweepParams['stepDelay'] = -1 # seconds (-1 for auto, nearly zero, delay)
    sweepParams['nplc'] = 0.5
    
    sm.write(':source:voltage {0:0.4f}'.format(sweepParams['sweepStart']))
    sm.write(':source:function voltage')
    sm.write(':output on')    
    sm.write(':source:voltage:mode sweep')
    sm.write(':source:sweep:spacing linear')
    if sweepParams['stepDelay'] == -1:
        sm.write(':source:delay:auto on') # this just sets delay to 1ms
    else:
        sm.write(':source:delay:auto off')
        sm.write(':source:delay {0:0.3f}'.format(sweepParams['stepDelay']))
    sm.write(':trigger:count {0:d}'.format(int(sweepParams['nPoints'])))
    sm.write(':source:sweep:points {0:d}'.format(int(sweepParams['nPoints'])))
    sm.write(':source:voltage:start {0:.4f}'.format(sweepParams['sweepStart']))
    sm.write(':source:voltage:stop {0:.4f}'.format(sweepParams['sweepEnd']))
    dV = sm.query_ascii_values(':source:voltage:step?')[0]
    
    #sm.write(':source:voltage:range {0:.4f}'.format(sweepParams['sweepStart']))
    sm.write(':source:sweep:ranging best')
    sm.write(':sense:current:protection {0:.6f}'.format(sweepParams['maxCurrent']))
    sm.write(':sense:current:range {0:.6f}'.format(sweepParams['maxCurrent']))
    sm.write(':sense:voltage:nplcycles {:}'.format(sweepParams['nplc']))
    sm.write(':sense:current:nplcycles {:}'.format(sweepParams['nplc']))
    sm.write(':display:digits 5')
    
    myPrint("Doing initial exploratory sweep...", file=sys.stderr, flush=True)
    sweepValues = sm.query_values('READ?')
    
    # deselect all pixels
    pcb.pix_picker(substrate, 0)
    
    myPrint("Exploratory sweep done!", file=sys.stderr, flush=True)
    
    sweepValues = numpy.reshape(sweepValues, (-1,4))
    v = sweepValues[:,0]
    i = sweepValues[:,1]
    t = sweepValues[:,2] - t0
    #p = v*i
    #Isc = i[-1]
    # derive new current limit from short circuit current
    #sm.write(':sense:current:range {0:.6f}'.format(Isc*1.2))
    
    # display initial sweep result
    for x in range(len(sweepValues)):
        myPrint('{:1d},{:.4e},{:.4e},{:.4e}'.format(exploring,t[x],v[x]*polarity,i[x]*polarity), flush=True)

