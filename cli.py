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
parser.add_argument('--xmas-lights', default=False, action='store_true', help="Connectivity test. Probs only run this with commercial LEDs.")
parser.add_argument("--sweep", default=False, action='store_true', help="Do an I-V sweep from Voc to Jsc")
parser.add_argument('--snaith', default=False, action='store_true', help="Do an I-V sweep from Jsc --> Voc")
parser.add_argument('--T_prebias', type=float, default=10, help="Wait this many seconds with the source on before sweeping")
parser.add_argument('--area', type=float, default=1.0, help="Specify device area in cm^2")
parser.add_argument('--mppt', type=float, default=0, help="Do maximum power point tracking for this many seconds")
parser.add_argument("--t_dwell", type=float, default=5, help="Total number of seconds for the dwell mppt phase(s)")

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
    q = sm.measureUntil(t_dwell=args.T_prebias, cb=streamCB)
    
    [Voc, Ioc, t0, status] = q.pop()
    [Voc, Ioc, t1, status] = q.popleft() # get the last entry
    
    # derive connection polarity
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
    sweepValues = sm.measure()
    
    myPrint("Exploratory sweep done!", file=sys.stderr, flush=True)
    
    sweepValues = numpy.reshape(sweepValues, (-1,4))
    v = sweepValues[:,0]
    i = sweepValues[:,1]
    t = sweepValues[:,2] - t0
    #p = v*i
    Isc = i[-1]
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
  q = sm.measureUntil(t_dwell=args.T_prebias, cb=streamCB)
    
  [Vsc, Isc, t0, status] = q.pop()
  [Vsc, Isc, t1, status] = q.popleft() # get the last entry
  
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
  sweepValues = sm.measure()
  
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

if args.mppt > 0:
    if not(args.snaith or args.sweep):
        raise ValueError('You must do a forward or reverse sweep before mppt')
    p = v*i*polarity
    maxIndex = numpy.argmax(p)
    Vmpp = v[maxIndex]
    myPrint("Initial Mpp found:", file=sys.stderr, flush=True)
    myPrint(p[maxIndex]*1000,"mW @",Vmpp,"V", file=sys.stderr, flush=True)
    myPrint("Teleporting back to Mpp...", file=sys.stderr, flush=True)
    dV = sm.dV
    sm.setupDC(sourceVoltage=True, compliance=0.04, setPoint=Vmpp)
    def streamCB(measurement):
        [v, i, now, status] = measurement
        t = now - t0
        myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, t, v*polarity, i*polarity), flush=True)
        myPrint("P={:.6f} mW".format(i*1000*v), file=sys.stderr, flush=True)
    #t_chill = 10
    #myPrint("Chilling for {:} seconds...".format(t_chill), file=sys.stderr, flush=True)
    #q = sm.measureUntil(t_dwell=10, cb=streamCB)
    #yPrint("Mp after chill:", file=sys.stderr, flush=True)
    #[v, i, now, status] = q.popleft()
    #myPrint("P={:.6f} mW".format(i*1000*v), file=sys.stderr, flush=True)
    
    # for curve exploration
    dAngleMax = 25 #[degrees] (plus and minus)
    while True:
        exploring = 0
        # dwell at Vmpp while measuring current
        tic = time.time()
        toc =  time.time() - tic
        myPrint("Dwelling @ Mpp for",args.t_dwell,"s...", file=sys.stderr, flush=True)
        myPrint("", file=sys.stderr, flush=True)
        q = sm.measureUntil(t_dwell=args.t_dwell, cb=streamCB)
        [v, i, now, status] = q.popleft()
      
        myPrint("Exploring for new Mpp...", file=sys.stderr, flush=True)
        exploring = 1
        i_explore = numpy.array(i)
        v_explore = numpy.array(v)
      
        dAngle = 0
        angleMpp = numpy.rad2deg(numpy.arctan(i/v*Voc/Isc))
        v_set = Vmpp
        switched = False
        myPrint("Walking up in voltage...", file=sys.stderr, flush=True)
        while dAngle < dAngleMax:
            v_set = v_set + dV
            sm.write(':source:voltage {0:0.4f}'.format(v_set))
            [v, i, tx, status] = sm.measure()
            i = i*polarity
            t_run = tx-t0
            myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring,t_run,v,i), flush=True)
            if t_run > args.mppt:
                sys.exit()
            i_explore = numpy.append(i_explore, i)
            v_explore = numpy.append(v_explore, v)
            dAngle = numpy.rad2deg(numpy.arctan(i/v*Voc/Isc)) - angleMpp
            if (dAngle < -dAngleMax) and not switched:
                myPrint("Upper exploration voltage limit reached.", file=sys.stderr, flush=True)
                myPrint("Walking down in voltage...", file=sys.stderr, flush=True)
                switched = True
                dV = dV * -1 # switch our voltage walking direction (only once)
      
        myPrint("Lower exploration voltage limit reached.", file=sys.stderr, flush=True)
      
        # find the powers for the values we just explored
        p_explore = v_explore*i_explore*polarity
        maxIndex = numpy.argmax(p_explore)
        Vmpp = v_explore[maxIndex]
      
        myPrint("New Mpp found:", file=sys.stderr, flush=True)
        myPrint(p_explore[maxIndex]*1000,"mW @",Vmpp,"V", file=sys.stderr, flush=True)    
      
        # now let's walk back to our new Vmpp
        dV = dV * -1
        v_set = v_set + dV
        myPrint("Walking back to Mpp...", file=sys.stderr, flush=True)
        while v_set < Vmpp:
            sm.write(':source:voltage {0:0.4f}'.format(v_set))
            [v, i, tx, status] = sm.query_ascii_values('READ?')
            i = i*polarity
            t_run = tx-t0
            myPrint('{:1d},{:.4e},{:.4e},{:.4e}'.format(exploring,t_run,v,i), flush=True)
            if t_run > args.t_total:
                sys.exit()
            v_set = v_set + dV
        sm.write(':source:voltage {0:0.4f}'.format(Vmpp))
        myPrint("Mpp reached.", file=sys.stderr, flush=True)    
      

# deselect all pixels
pcb.pix_picker(substrate, 0)