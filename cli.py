#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# written by grey@christoforo.net

#from toolkit import k2400
#from toolkit import pcb
#from toolkit import virt
from toolkit import logic
#from toolkit import wavelabs

import sys
import argparse
import time
import numpy
import mpmath
import os
from scipy import special
from collections import deque

class FullPaths(argparse.Action):
  """Expand user- and relative-paths"""
  def __call__(self, parser, namespace, values, option_string=None):
    setattr(namespace, self.dest, os.path.abspath(os.path.expanduser(values)))

def is_dir(dirname):
  """Checks if a path is an actual directory"""
  if not os.path.isdir(dirname):
    msg = "{0} is not a directory".format(dirname)
    raise argparse.ArgumentTypeError(msg)
  else:
    return dirname

def get_args():
  """Get CLI arguments and options"""
  parser = argparse.ArgumentParser(description='Automated solar cell IV curve collector using a Keithley 2400 sourcemeter. Data is written to stdout and human readable messages are written to stderr.')

  parser.add_argument("address", nargs='?', default="None", type=str, help="VISA resource name for sourcemeter")
  parser.add_argument("switch_address", nargs='?', default=None, type=str, help="IP address for PCB")
  parser.add_argument("pixel_address", nargs='?', default=None, type=str, help="Pixel to scan (A1, A2,...), use hex (0x...) to specify an enabled pixel bitmask")
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
  parser.add_argument('--no_wavelabs', default=False, action='store_true', help="WaveLabs LED solar sim is not present")  
  parser.add_argument('--t_prebias', type=float, default=10, help="Number of seconds to sit at initial voltage value before doing sweep")
  parser.add_argument('--area', type=float, default=-1.0, help="Specify device area in cm^2")
  parser.add_argument('--mppt', type=int, default=0, help="Do maximum power point tracking for this many cycles")
  parser.add_argument("--t_dwell", type=float, default=15, help="Total number of seconds for the dwell mppt phase(s)")  
  parser.add_argument('--destination', help="Save output files here", action=FullPaths, type=is_dir)

  return parser.parse_args()

args = get_args()

args.terminator = bytearray.fromhex(args.terminator).decode()

# create the control entity
l = logic(saveDir = args.destination)

if args.area != -1.0:
  l.cli_area = args.area

# connect to PCB and sourcemeter
<<<<<<< HEAD
l.connect(dummy=args.dummy, visa_lib=args.visa_lib, visaAddress=args.address, no_wavelabs=args.no_wavelabs
         pcbAddress=args.switch_address, terminator=args.terminator, serialBaud=args.baud)
=======
l.connect(dummy=args.dummy, visa_lib=args.visa_lib, visaAddress=args.address, 
          pcbAddress=args.switch_address, terminator=args.terminator, serialBaud=args.baud)
>>>>>>> 194f5feda2252e49e06301a41be80925ba446d35

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

def buildQ(pixel_address_string):
  """Generates a queue containing pixel addresses we'll run through
  if pixel_address_string starts with 0x, decode it as a hex value where
  a 1 in a position means that pixel is enabled
  the leftmost byte here is for substrate A
  """
  q = deque()
  if pixel_address_string[0:2] == '0x':
    bitmask = bytearray.fromhex(pixel_address_string[2:])
    for substrate_index, byte in enumerate(bitmask):
      substrate = chr(substrate_index+ord('A'))
      for i in range(8):
        mask =  1 << i
        if (byte & mask):
          q.append(substrate+str(i+1))
  else:
    q.append(pixel_address_string)

  return q

if args.pixel_address is not None:
  pixel_address_que = buildQ(args.pixel_address)

if args.sweep or args.snaith or args.mppt > 0:
  # connect to light engine and activate recipe
  #wl = wavelabs()
  #wl.startServer()
  #wl.awaitConnection()
  #wl.activateRecipe('WL-Test')

  l.runSetup(operator='grey')
  last_substrate = None
  for pixel_address in pixel_address_que:
    substrate = pixel_address[0]
    pix = pixel_address[1]
    print('\nOperating on substrate {:s}, pixel {:s}...'.format(substrate, pix))
    if last_substrate != substrate:  # we have a new substrate
      print('New substrate!')
      last_substrate = substrate
      l.substrateSetup(position=substrate)

    #wl.startRecipe()
    l.pixelSetup(pix, t_dwell_voc = args.t_prebias)
    #wl.cancelRecipe()

#if False and (args.sweep or args.snaith or mppt > 0):
    #substrate = args.pixel_address[0]
    #pix = args.pixel_address[1]

    #if not pcb.pix_picker(substrate, pix):
      #raise ValueError('Unable to select desired pixel')
    ## let's find our open circuit voltage
    #sm.setNPLC(10)
    #sm.setupDC(sourceVoltage=False, compliance=2, setPoint=0)
    #sm.write(':arm:source immediate') # this sets up the trigger/reading method we'll use below

    #myPrint("Measuring Voc..", file=sys.stderr, flush=True)
    #def streamCB(measurement):
      #[Voc, Ioc, now, status] = measurement
      #myPrint("Voc = {:.6f} V".format(Voc), file=sys.stderr, flush=True)
    #q = sm.measureUntil(t_dwell=args.t_prebias, cb=streamCB)
    ##vMax = float(sm.sm.query(':sense:voltage:range?'))

    #[Voc, Ioc, t0, status] = q.popleft()  # get the oldest entry
    #[Voc, Ioc, tx, status] = q.pop() # get the most recent entry
    #myPrint("Voc is {:.6f} V".format(Voc), file=sys.stderr, flush=True)

    ## derive connection polarity
    #if Voc < 0:
        #vPol = -1
        #iPol = 1
    #else:
        #vPol = 1
        #iPol = -1

    #exploring = 1

    #myPrint('# i-v file format v1', flush=True)
    #myPrint('# Area = {:}'.format(args.area))
    #myPrint('# exploring\ttime\tvoltage\tcurrent', flush=True)
    #myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, tx - t0, Voc*vPol, Ioc*iPol), flush=True)

    if args.sweep:
      start = l.Voc
      end = 0
      points = 101
      message = 'Sweeping voltage from {:.0f} mV to {:.0f} mV'.format(start*1000, end*1000)
      #wl.startRecipe()
      sv = l.sweep(sourceVoltage=True, compliance=0.04, senseRange='f', nPoints=points, start=start, end=end, NPLC=1, message=message)
      #wl.cancelRecipe()
      roi_start = len(l.m) - len(sv)
      roi_end = len(l.m) - 1
      l.addROI(roi_start, roi_end, 'Sweep')
      #l.f[l.position+'/'+l.pixel].create_dataset('Sweep', data=sv)

    #if args.sweep and False:
      ## for initial sweep
      ###NOTE: what if Isc degrades the device? maybe I should only sweep backwards
      ###until the power output starts dropping instead of going all the way to zero volts...
      #sm.setNPLC(0.5)
      #points = 1001
      #sm.setupSweep(sourceVoltage=True, compliance=0.04, nPoints=points,
                    #stepDelay=-1, start=Voc, end=0)

      #myPrint("Performing I-V sweep...", file=sys.stderr, flush=True)
      #sweepValues = sm.measure()

      #myPrint("Sweep done!", file=sys.stderr, flush=True)

      #sweepValues = numpy.reshape(sweepValues, (-1,4))
      #v = sweepValues[:,0]
      #i = sweepValues[:,1]
      #t = sweepValues[:,2] - t0

      ## display initial sweep result
      #for x in range(len(sweepValues)):
          #myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, t[x], v[x]*vPol, i[x]*iPol), flush=True)

    if args.sweep or args.snaith:
      # let's find our sc current now
      #wl.startRecipe()
      iscs = l.steadyState(t_dwell=args.t_dwell, NPLC = 10, sourceVoltage=True, compliance=0.04, senseRange ='a', setPoint=0)
      #wl.cancelRecipe()

      l.Isc = iscs[-1][1]  # take the last measurement to be Isc

      l.f[l.position+'/'+l.pixel].attrs['Isc'] = l.Isc
      roi_start = len(l.m) - len(iscs)
      roi_end = len(l.m) - 1
      l.addROI(roi_start, roi_end, 'I_sc Dwell')

      #l.f[l.position+'/'+l.pixel].create_dataset('IscDwell', data=iscs)

      #sm.setNPLC(10)
      #sm.setupDC(sourceVoltage=True, compliance=0.04, setPoint=0)
      #sm.write(':arm:source immediate') # this sets up the trigger/reading method we'll use below

      #exploring = 1
      #myPrint("Measuring Isc...", file=sys.stderr, flush=True)
      #def streamCB(measurement):
          #[Vsc, Isc, now, status] = measurement
          #myPrint("Isc = {:.6f} mA".format(Isc*1000), file=sys.stderr, flush=True)
      #q = sm.measureUntil(t_dwell=args.t_prebias, cb=streamCB)
      #iMax = float(sm.sm.query(':sense:current:range?'))

      #[Vsc, Isc, tx, status] = q.pop() # get the most recent entry

      #myPrint("Isc is {:.6f} mA".format(Isc*1000), file=sys.stderr, flush=True)
      #myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, tx - t0 ,Vsc*vPol, Isc*iPol), flush=True)

    if args.snaith:
      # for initial sweep
      ##NOTE: what if Isc degrades the device? maybe I should only sweep backwards
      ##until the power output starts dropping instead of going all the way to zero volts...

      start = 0
      end = l.Voc * (1 + l.percent_beyond_voc / 100)
      points = 101
      message = 'Snaithing voltage from {:.0f} mV to {:.0f} mV'.format(start*1000, end*1000)

      # if the measured Isc is below 5 microamps, set the compliance to 10
      # we don't need the accuracy of the lowest current sense range and we'd rather have the compliance headroom
      if abs(l.Isc) < 0.000005:
        compliance = 0.00001
      else:
        compliance = l.Isc*1.5
      #wl.startRecipe()
      sv = l.sweep(sourceVoltage=True, senseRange='f', compliance=compliance, nPoints=points, start=start, end=end, NPLC=1, message=message)
      #wl.cencelRecipe()
      roi_start = len(l.m) - len(sv)
      roi_end = len(l.m) - 1
      l.addROI(roi_start, roi_end, 'Snaith')
      #l.f[l.position+'/'+l.pixel].create_dataset('Snaith', data=sv)  

      #sm.setNPLC(0.5)
      #points = 1001
      #sm.setupSweep(sourceVoltage=True, compliance=0.04, nPoints=points, stepDelay=-1, start=0, end=Voc, senseRange=iMax)

      #myPrint("Performing I-V snaith...", file=sys.stderr, flush=True)
      #sweepValues = sm.measure()

      #myPrint("Snaith done!", file=sys.stderr, flush=True)

      #sweepValues = numpy.reshape(sweepValues, (-1,4))
      #v = sweepValues[:,0]
      #i = sweepValues[:,1]
      #t = sweepValues[:,2] - t0

      ## display initial sweep result
      #for x in range(len(sweepValues)):
          #myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(exploring, t[x], v[x]*vPol, i[x]*iPol), flush=True)

      #exit()

    def mpptCB(measurement):
      """Callback function for max powerpoint tracker
      """
      [v, i, now, status] = measurement
      t = now - t0
      myPrint('{:1d},{:.6f},{:.6f},{:.6f}'.format(0, t, v*vPol, i*iPol), flush=True)
      #myPrint("P={:.6f} mW".format(i*1000*v*-1), file=sys.stderr, flush=True)

    if (args.mppt > 0) and False:
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

    l.pixelComplete()
  l.runDone()
sm.outOn(on=False)
sm.sm.close()
print("done!")
