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
parser.add_argument("--sweep", default=False, action='store_true', help="Do an I-V sweep from Voc to Jsc")
parser.add_argument("--front", default=False, action='store_true', help="Use the front terminals")
parser.add_argument("--two-wire", default=False, dest='twoWire', action='store_true', help="Use two wire mode")
parser.add_argument("--terminator", type=str, default='0A', help="Instrument comms read & write terminator (enter in hex)")
parser.add_argument("--baud", type=int, default=57600, help="Instrument serial comms baud rate")
parser.add_argument("--port", type=int, default=23, help="Port to connect to switch hardware")
parser.add_argument('--xmas-lights', default=False, action='store_true', help="Connectivity test. Probs only run this with commercial LEDs.")
parser.add_argument('--snaith', default=False, action='store_true', help="Do an I-V sweep from Jsc --> Voc")
parser.add_argument('--T_prebias', type=float, default=10, help="Wait this many seconds with the source on before sweeping")
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

substrate = args.pixel_address[0] 
if args.xmas_lights:
    myPrint("LED test mode active on substrate {:s}".format(substrate), file=sys.stderr, flush=True)
    
    sweepHigh = 0.01 # amps
    sweepLow = 0 # amps
    
    pcb.pix_picker(substrate,1)
    sm.setNPLC(0.01)
    sm.setupSweep(sourceVoltage=False, compliance=2.5, nPoints=101, stepDelay=-1, start=sweepLow, end=sweepHigh)
    sm.write(':arm:source bus') # this allows for the trigger style we'll use here
    
    substrate = args.pixel_address[0]
    for pix in range(8):
        pcb.pix_picker(substrate,pix+1)
        
        sm.updateSweepStart(sweepLow)
        sm.updateSweepStop(sweepHigh)
        sm.arm()
        sm.trigger()
        sm.opc()
        
        sm.updateSweepStart(sweepHigh)
        sm.updateSweepStop(sweepLow)
        sm.arm()
        sm.trigger()
        sm.opc()
        
        # off during pix switchover
        sm.setOutput(0)
    
    sm.outOn(False)
    
    # deselect all pixels
    pcb.pix_picker(substrate, 0)

if args.sweep or args.snaith:
    pix = args.pixel_address[1] 
    # let's find our open circuit voltage
    if not pcb.pix_picker(substrate, pix):
      raise ValueError('Unable to select desired pixel')
    sm.setNPLC(10)
    sm.setupDC(sourceVoltage=False, compliance=2, setPoint=0)
    sm.write(':arm:source immediate') # this sets up the trigger/reading method we'll use below
    
    exploring = 1
    myPrint("Measuring Voc:", file=sys.stderr, flush=True)
    def streamCB(measurement):
      [Voc, Ioc, now, status] = measurement
      myPrint("{:.6f} V".format(Voc), file=sys.stderr, flush=True)
    sm.initStreamMeasure(t_dwell=args.T_prebias, cb=streamCB)
    
    [Voc, Ioc, t0, status] = sm.outQ.get(timeout=5) # need this for t0
    while sm.busy:
      pass
    sm.thread.join()
    unpackedQ = [sm.outQ.get() for i in range(sm.outQ.qsize())]
    [Voc, Ioc, t1, status] = unpackedQ[-1] # get the last entry
    
    # derive connection polarity from Voc
    if Voc < 0:
        polarity = -1
    else:
        polarity = 1    

if args.sweep:
    #sm.write(':output off')
    myPrint('#exploring,time,voltage,current', file=sys.stderr, flush=True)
    
    myPrint('# i-v file format v1', flush=True)
    myPrint('# Area = {:}'.format(args.area))
    myPrint('# exploring\ttime\tvoltage\tcurrent', flush=True)
    myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, t1 - t0 ,Voc*polarity, Ioc*polarity), flush=True)
    
    # for initial sweep
    ##NOTE: what if Isc degrades the device? maybe I should only sweep backwards
    ##until the power output starts dropping instead of going all the way to zero volts...
    sm.setNPLC(0.5)
    points = 1001
    sm.setupSweep(sourceVoltage=True, compliance=0.04, nPoints=points, stepDelay=-1, start=Voc, end=0)

    myPrint("Doing initial exploratory sweep...", file=sys.stderr, flush=True)
    sm.initStreamMeasure(measurements=1)

    while sm.busy:
      pass
    sm.thread.join()    

    sweepValues = sm.outQ.get()
    
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
        myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring,t[x],v[x]*polarity,i[x]*polarity), flush=True)

if args.snaith:
  sm.setNPLC(10)
  sm.setupDC(sourceVoltage=True, compliance=0.04, setPoint=0)
  sm.write(':arm:source immediate') # this sets up the trigger/reading method we'll use below
  
  exploring = 1
  myPrint("Measuring Isc:", file=sys.stderr, flush=True)
  def streamCB(measurement):
    [Vsc, Isc, now, status] = measurement
    myPrint("{:.6f} mA".format(Isc*1000), file=sys.stderr, flush=True)
  sm.initStreamMeasure(t_dwell=args.T_prebias, cb=streamCB)
  
  [Vsc, Isc, t0, status] = sm.outQ.get(timeout=5) # need this for t0
  while sm.busy:
    pass
  sm.thread.join()
  unpackedQ = [sm.outQ.get() for i in range(sm.outQ.qsize())]
  [Vsc, Isc, t1, status] = unpackedQ[-1] # get the last entry  
  #sm.write(':output off')
  myPrint('#exploring,time,voltage,current', file=sys.stderr, flush=True)
  
  myPrint('# i-v file format v1', flush=True)
  myPrint('# Area = {:}'.format(args.area))
  myPrint('# exploring\ttime\tvoltage\tcurrent', flush=True)
  myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, t1 - t0 ,Vsc*polarity, Isc*polarity), flush=True)
  
  # for initial sweep
  ##NOTE: what if Isc degrades the device? maybe I should only sweep backwards
  ##until the power output starts dropping instead of going all the way to zero volts...
  sm.setNPLC(0.5)
  points = 1001
  sm.setupSweep(sourceVoltage=True, compliance=0.04, nPoints=points, stepDelay=-1, start=0, end=Voc)

  myPrint("Doing initial exploratory sweep...", file=sys.stderr, flush=True)
  sm.initStreamMeasure(measurements=1)

  while sm.busy:
    pass
  sm.thread.join()    

  sweepValues = sm.outQ.get()
  
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
      myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring,t[x],v[x]*polarity,i[x]*polarity), flush=True)



# deselect all pixels
pcb.pix_picker(substrate, 0)