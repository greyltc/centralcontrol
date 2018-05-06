#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# written by grey@christoforo.net

from toolkit import k2400
from toolkit import pcb
from toolkit import virt
from toolkit import logic

import sys
import argparse
import time
import numpy
import mpmath
from scipy import special
parser = argparse.ArgumentParser(description='Automated solar cell IV curve collector using a Keithley 2400 sourcemeter. Data is written to stdout and human readable messages are written to stderr.')

parser.add_argument("address", nargs='?', default="None", type=str, help="VISA resource name for sourcemeter")
parser.add_argument("switch_address", nargs='?', default=None, type=str, help="IP address for PCB")
parser.add_argument("pixel_address", nargs='?', default=None, type=str, help="Pixel to scan (A1, A2,...)")
parser.add_argument('--dummy', default=False, action='store_true', help="Run in dummy mode (doesn't need sourcemeter, generates simulated device data)")
parser.add_argument('--visa_lib', type=str, default='@py', help="Path to visa library in case pyvisa can't find it, try C:\\Windows\\system32\\visa64.dll")
parser.add_argument('--file', type=str, help="Write output data stream to this file in addition to stdout.")
parser.add_argument("--scan", default=False, action='store_true', help="Scan for obvious VISA resource names, print them and exit")
parser.add_argument("--front", default=False, action='store_true', help="Use the front terminals")
parser.add_argument("--two-wire", default=False, dest='twoWire', action='store_true', help="Use two wire mode")
parser.add_argument("--terminator", type=str, default='0A', help="Instrument comms read & write terminator (enter in hex)")
parser.add_argument("--baud", type=int, default=57600, help="Instrument serial comms baud rate")
parser.add_argument("--port", type=int, default=23, help="Port to connect to switch hardware")
parser.add_argument('--test-hardware', default=False, action='store_true', help="Exercises all the hardware")
parser.add_argument("--sweep", default=False, action='store_true', help="Do an I-V sweep from Voc to Jsc")
parser.add_argument('--snaith', default=False, action='store_true', help="Do an I-V sweep from Jsc --> Voc")
parser.add_argument('--t_prebias', type=float, default=10, help="Number of seconds to sit initial voltage value before doing sweep")
parser.add_argument('--area', type=float, default=1.0, help="Specify device area in cm^2")
parser.add_argument('--mppt', type=int, default=0, help="Do maximum power point tracking for this many cycles")
parser.add_argument("--t_dwell", type=float, default=15, help="Total number of seconds for the dwell mppt phase(s)")

args = parser.parse_args()

args.terminator = bytearray.fromhex(args.terminator).decode()

# create the control entity
l = logic()

# connect to PCB and sourcemeter
l.connect(dummy=args.dummy, visa_lib=args.visa_lib, visaAddress=args.address, 
         pcbAddress=args.switch_address, terminator=args.terminator, serialBaud=args.baud)

if args.dummy:
  args.pixel_address = 'A1'
else:
  if args.front:
    l.sm.setTerminals(front=args.front)
  if args.twoWire:
    l.sm.setWires(twoWire=args.twoWire)

if args.test_hardware:
  l.hardwareTest()
  
sm = l.sm
pcb = l.pcb

dataDestinations = [sys.stdout]
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

if args.sweep or args.snaith or mppt > 0:
    substrate = args.pixel_address[0]
    pix = args.pixel_address[1]
    
    if not pcb.pix_picker(substrate, pix):
      raise ValueError('Unable to select desired pixel')
    # let's find our open circuit voltage
    sm.setNPLC(10)
    sm.setupDC(sourceVoltage=False, compliance=2, setPoint=0)
    sm.write(':arm:source immediate') # this sets up the trigger/reading method we'll use below

    myPrint("Measuring Voc..", file=sys.stderr, flush=True)
    def streamCB(measurement):
      [Voc, Ioc, now, status] = measurement
      myPrint("Voc = {:.6f} V".format(Voc), file=sys.stderr, flush=True)
    q = sm.measureUntil(t_dwell=args.t_prebias, cb=streamCB)
    #vMax = float(sm.sm.query(':sense:voltage:range?'))
    
    [Voc, Ioc, t0, status] = q.popleft()  # get the oldest entry
    [Voc, Ioc, tx, status] = q.pop() # get the most recent entry
    myPrint("Voc is {:.6f} V".format(Voc), file=sys.stderr, flush=True)

    # derive connection polarity
    if Voc < 0:
        vPol = -1
        iPol = 1
    else:
        vPol = 1
        iPol = -1
        
    exploring = 1
  
    myPrint('# i-v file format v1', flush=True)
    myPrint('# Area = {:}'.format(args.area))
    myPrint('# exploring\ttime\tvoltage\tcurrent', flush=True)
    myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, tx - t0, Voc*vPol, Ioc*iPol), flush=True)

if args.sweep:
    # for initial sweep
    ##NOTE: what if Isc degrades the device? maybe I should only sweep backwards
    ##until the power output starts dropping instead of going all the way to zero volts...
    sm.setNPLC(0.5)
    points = 1001
    sm.setupSweep(sourceVoltage=True, compliance=0.04, nPoints=points, stepDelay=-1, start=Voc, end=0)

    myPrint("Performing I-V sweep...", file=sys.stderr, flush=True)
    sweepValues = sm.measure()

    myPrint("Sweep done!", file=sys.stderr, flush=True)

    sweepValues = numpy.reshape(sweepValues, (-1,4))
    v = sweepValues[:,0]
    i = sweepValues[:,1]
    t = sweepValues[:,2] - t0

    # display initial sweep result
    for x in range(len(sweepValues)):
        myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, t[x], v[x]*vPol, i[x]*iPol), flush=True)
    
if args.sweep or args.snaith:
  # let's find our sc current now
  sm.setNPLC(10)
  sm.setupDC(sourceVoltage=True, compliance=0.04, setPoint=0)
  sm.write(':arm:source immediate') # this sets up the trigger/reading method we'll use below

  exploring = 1
  myPrint("Measuring Isc...", file=sys.stderr, flush=True)
  def streamCB(measurement):
      [Vsc, Isc, now, status] = measurement
      myPrint("Isc = {:.6f} mA".format(Isc*1000), file=sys.stderr, flush=True)
  q = sm.measureUntil(t_dwell=args.t_prebias, cb=streamCB)
  iMax = float(sm.sm.query(':sense:current:range?'))

  [Vsc, Isc, tx, status] = q.pop() # get the most recent entry
  
  myPrint("Isc is {:.6f} mA".format(Isc*1000), file=sys.stderr, flush=True)
  myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, tx - t0 ,Vsc*vPol, Isc*iPol), flush=True)  

if args.snaith:
  # for initial sweep
  ##NOTE: what if Isc degrades the device? maybe I should only sweep backwards
  ##until the power output starts dropping instead of going all the way to zero volts...
  sm.setNPLC(0.5)
  points = 1001
  sm.setupSweep(sourceVoltage=True, compliance=0.04, nPoints=points, stepDelay=-1, start=0, end=Voc, senseRange=iMax)

  myPrint("Performing I-V snaith...", file=sys.stderr, flush=True)
  sweepValues = sm.measure()

  myPrint("Snaith done!", file=sys.stderr, flush=True)

  sweepValues = numpy.reshape(sweepValues, (-1,4))
  v = sweepValues[:,0]
  i = sweepValues[:,1]
  t = sweepValues[:,2] - t0

  # display initial sweep result
  for x in range(len(sweepValues)):
      myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, t[x], v[x]*vPol, i[x]*iPol), flush=True)

def mpptCB(measurement):
    """Callback function for max powerpoint tracker
    """
    [v, i, now, status] = measurement
    t = now - t0
    myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(0, t, v*vPol, i*iPol), flush=True)
    #myPrint("P={:.6f} mW".format(i*1000*v*-1), file=sys.stderr, flush=True)

if args.mppt > 0:
    myPrint("Starting maximum power point tracker", file=sys.stderr, flush=True)
    if not(args.snaith or args.sweep):
        print("Warning: doing max power point tracking without prior sweep")
        Vmpp = 0.7
        dV = Voc / 1001
        iMax = 0.04
    else:
        # find mpp from the previous sweep
        p = v*i*-1
        maxIndex = numpy.argmax(p)
        Vmpp = v[maxIndex]
        
        # use previous voltage step
        dV = sm.dV

    # switch to fixed DC mode
    sm.setupDC(sourceVoltage=True, compliance=iMax, setPoint=Vmpp, senseRange=iMax)

    # set exploration limits
    dAngleMax = 3 #[degrees] (plus and minus)
    
    mpptCyclesRemaining = args.mppt

    while (True):
        exploring = 0
        myPrint("Teleporting to Mpp...", file=sys.stderr, flush=True)
        sm.setOutput(Vmpp)
        # dwell at Vmpp while measuring current
        myPrint("Dwelling @ Mpp for {:} seconds...".format(args.t_dwell), file=sys.stderr, flush=True)
        q = sm.measureUntil(t_dwell=args.t_dwell, cb=mpptCB)

        [Vmpp, Impp, now, status] = q.pop() # get the most recent entry
        if mpptCyclesRemaining == 0:
            print('Steady state max power was {:0.4f} mW @ {:0.2f} mV'.format(Vmpp*Impp*1000*-1, Vmpp*1000*vPol))
            break        

        myPrint("Exploring for new Mpp...", file=sys.stderr, flush=True)
        exploring = 1
        i_explore = numpy.array(Impp)
        v_explore = numpy.array(Vmpp)

        angleMpp = numpy.rad2deg(numpy.arctan(Impp/Vmpp*Voc/Isc))
        #print('MPP ANGLE = {:}'.format(angleMpp))
        v_set = Vmpp
        edgeANotTouched = True
        edgeBNotTouched = True
        while (edgeANotTouched or edgeBNotTouched):
            v_set = v_set + dV
            sm.write(':source:voltage {0:0.6f}'.format(v_set))
            [v, i, tx, status] = sm.measure()

            myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, tx - t0, v*vPol, i*iPol), flush=True)
            i_explore = numpy.append(i_explore, i)
            v_explore = numpy.append(v_explore, v)
            thisAngle = numpy.rad2deg(numpy.arctan(i/v*Voc/Isc))
            #print('This Angle = {:}'.format(thisAngle))
            dAngle = thisAngle - angleMpp
            if ((dAngle > dAngleMax) or (v_set >= Voc)) and ( edgeANotTouched ): # twords Voc edge
                edgeANotTouched = False
                if (v_set >= Voc):
                    because = 'Voc reached'
                else:
                    because = 'angle limit reached'
                if edgeBNotTouched:
                    myPrint("Bouncing off Edge A because {:}".format(because), file=sys.stderr, flush=True)
                    dV = dV * -1 # switch our voltage walking direction
                else:
                    myPrint("Second edge (A) reached because {:}".format(because), file=sys.stderr, flush=True)
            if ((-dAngle > dAngleMax) or (v_set <= 0)) and ( edgeBNotTouched ): # towards Isc (V=0) edge
                edgeBNotTouched = False
                if (v_set <= 0):
                    because = 'V=0 reached'
                else:
                    because = 'angle limit reached'
                if edgeANotTouched:
                    myPrint("Bouncing off Edge B because {:}".format(because), file=sys.stderr, flush=True)
                    dV = dV * -1 # switch our voltage walking direction
                else:
                    myPrint("Second edge (B) reached because {:}".format(because), file=sys.stderr, flush=True)


        myPrint("Done exploring.", file=sys.stderr, flush=True)

        # find the powers for the values we just explored
        p_explore = v_explore * i_explore * -1
        maxIndex = numpy.argmax(p_explore)
        Vmpp = v_explore[maxIndex]
        Impp = i_explore[maxIndex]

        myPrint("New Mpp found: {:.6f} mW @ {:.6f} V".format(p_explore[maxIndex]*1000, Vmpp), file=sys.stderr, flush=True)

        dFromLastMppAngle = numpy.rad2deg(numpy.arctan(Impp/Vmpp*Voc/Isc)) - angleMpp

        myPrint("That's: {:.6f} degrees from the previous Mpp.".format(dFromLastMppAngle), file=sys.stderr, flush=True)
        mpptCyclesRemaining =  mpptCyclesRemaining - 1
        

sm.outOn(on=False)