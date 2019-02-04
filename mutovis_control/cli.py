#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# written by grey@mutovis.com

from mutovis_control import logic

import sys
import argparse
import time
import numpy
import mpmath
import os
import distutils.util

import appdirs
import configparser
import ast
import pathlib

from collections import deque

# for updating prefrences
prefs = {} # TODO: figure out how to un-global this

class cli:
  """the command line interface"""
  appname = 'mutovis_control_software'
  config_section = 'PREFRENCES'
  
  class FullPaths(argparse.Action):
    """Expand user- and relative-paths and save pref arg parse action"""
    def __call__(self, parser, namespace, values, option_string=None):
      value = os.path.abspath(os.path.expanduser(values))
      setattr(namespace, self.dest, value)
      prefs[self.dest] = value
      
  class RecordPref(argparse.Action):
    """save pref arg parse action"""
    def __call__(self, parser, namespace, values, option_string=None):
      setattr(namespace, self.dest, values)
      if values != None:  # don't save None params to prefs
        prefs[self.dest] = values  
  
  def __init__(self):
    self.args = self.get_args()
    
    # for saving config
    #config_path_string = appdirs.user_config_dir(self.appname) + os.path.sep 
    config_file_fullpath = appdirs.user_config_dir(self.appname) + os.path.sep + 'prefs.ini'
    config_path = pathlib.Path(config_file_fullpath)
    config_path.parent.mkdir(parents = True, exist_ok = True)
    config = configparser.ConfigParser()
    config.read(config_file_fullpath)
    
    # take command line args and put them in to prefrences
    if self.config_section not in config:
      config[self.config_section] = prefs
    else:
      for key, val in prefs.items():
        config[self.config_section][key] = str(val)
    
    # save the prefrences file
    with open(config_file_fullpath, 'w') as configfile:
      config.write(configfile)
    
    # now read back the new prefs
    config.read(config_file_fullpath)
    
    # TODO: display to user what args are being taken from the command line,
    # and which ones are being taken from the saved prefrences file
    
    # apply prefrences to argparse
    for key, val in config[self.config_section].items():
      if type(self.args.__getattribute__(key)) == int:
        self.args.__setattr__(key, config.getint(self.config_section, key))
      elif type(self.args.__getattribute__(key)) == float:
        self.args.__setattr__(key, config.getfloat(self.config_section, key))
      elif type(self.args.__getattribute__(key)) == bool:
        self.args.__setattr__(key, config.getboolean(self.config_section, key))
      elif key == 'diode_calibration':
        dc = config.get(self.config_section, key)
        self.args.__setattr__(key, ast.literal_eval(dc))
      else:
        self.args.__setattr__(key, config.get(self.config_section, key))

  def run(self):
    args = self.args
    if args.test_hardware:
      args.snaith = False
      args.sweep = False
      args.mppt = 0
      if "pixel_address" not in prefs:  # if we're in hardware test mode and no pixel has been specified from the command line, then test all connected substrates
        args.pixel_address = None
    
    args.terminator = bytearray.fromhex(args.terminator).decode()
    
    # create the control entity
    l = logic(saveDir = args.destination, ignore_diodes=args.ignore_diodes, diode_calibration=args.diode_calibration)
    
    if args.area != -1.0:
      l.cli_area = args.area
    
    # connect to PCB and sourcemeter
    l.connect(dummy=args.dummy, visa_lib=args.visa_lib, visaAddress=args.address, wavelabs=args.wavelabs, pcbAddress=args.switch_address, terminator=args.terminator, serialBaud=args.baud, waveLabsRelayIP=args.relay_ip)
    
    if args.dummy:
      args.pixel_address = 'A1'
    else:
      if args.rear == False:
        l.sm.setTerminals(front=True)
      if args.four_wire == False:
        l.sm.setWires(twoWire=True)
        
    if args.pixel_address is not None:
      pixel_address_que = self.buildQ(args.pixel_address)
    else:
      pixel_address_que = None

    if args.test_hardware:
      if pixel_address_que is None:
        holders_to_test = l.pcb.substratesConnected
      else:
        #turn the address que into a string of substrates
        mash = ''
        for pix in pixel_address_que:
          mash = mash + pix
        # delete the numbers
        mash = mash.translate({48:None,49:None,50:None,51:None,52:None,53:None,54:None,55:None,56:None})
        holders_to_test = ''.join(sorted(set(mash))) # remove dupes
      l.hardwareTest(holders_to_test)
    
    sm = l.sm
    pcb = l.pcb
    wl = l.wl
    
    if args.sweep or args.snaith or args.mppt > 0:
      l.runSetup(operator=args.operator)
      last_substrate = None
      for pixel_address in pixel_address_que:
        substrate = pixel_address[0]
        pix = pixel_address[1]
        print('\nOperating on substrate {:s}, pixel {:s}...'.format(substrate, pix))
        if last_substrate != substrate:  # we have a new substrate
          print('New substrate!')
          last_substrate = substrate
          substrate_ready = l.substrateSetup(position=substrate)
    
        pixel_ready = l.pixelSetup(pix, t_dwell_voc = args.t_prebias)  #  steady state Voc measured here
        if pixel_ready and substrate_ready:
          if args.sweep:
            # now sweep from Voc --> Isc
            if type(args.scan_high_override) == float:
              start = args.scan_high_override
            else:
              start = l.Voc
            if type(args.scan_low_override) == float:
              end = args.scan_low_override
            else:
              end = 0
              
            if type(args.current_compliance_override) == float:
              compliance = args.current_compliance_override
            else:
              compliance = 0.04  # we have to just guess what the current complaince here
              # TODO: probably need the user to tell us when it's a dark scan to get the sensativity we need in that case
    
            message = 'Sweeping voltage from {:.0f} mV to {:.0f} mV'.format(start*1000, end*1000)
            sv = l.sweep(sourceVoltage=True, compliance=compliance, senseRange='f', nPoints=args.scan_points, start=start, end=end, NPLC=args.scan_nplc, message=message)
            roi_start = len(l.m) - len(sv)
            roi_end = len(l.m) - 1
            l.addROI(roi_start, roi_end, 'Sweep')
    
          # steady state Isc measured here
          iscs = l.steadyState(t_dwell=args.t_dwell, NPLC = 10, sourceVoltage=True, compliance=0.04, senseRange ='a', setPoint=0)
    
          l.Isc = iscs[-1][1]  # take the last measurement to be Isc
    
          l.f[l.position+'/'+l.pixel].attrs['Isc'] = l.Isc
          roi_start = len(l.m) - len(iscs)
          roi_end = len(l.m) - 1
          l.addROI(roi_start, roi_end, 'I_sc Dwell')
      
          if args.snaith:
            # "snaithing" is a sweep from Isc --> Voc * (1+ l.percent_beyond_voc)
            if type(args.scan_low_override) == float:
              start = args.scan_low_override
            else:
              start = 0
            if type(args.scan_high_override) == float:
              end = args.scan_high_override
            else:
              end = l.Voc * ((100 + l.percent_beyond_voc) / 100)
    
            message = 'Snaithing voltage from {:.0f} mV to {:.0f} mV'.format(start*1000, end*1000)
      
            # if the measured steady state Isc was below 5 microamps, set the compliance to 10uA (this is probaby a dark curve)
            # we don't need the accuracy of the lowest current sense range (I think) and we'd rather have the compliance headroom
            # otherwise, set it to be 2x of Isc
            
            if type(args.current_compliance_override) == float:
              compliance = args.current_compliance_override
            else:
              if abs(l.Isc) < 0.000005:
                compliance = 0.00001
              else:
                compliance = l.Isc * 2
            sv = l.sweep(sourceVoltage=True, senseRange='f', compliance=compliance, nPoints=args.scan_points, start=start, end=end, NPLC=args.scan_nplc, message=message)
            roi_start = len(l.m) - len(sv)
            roi_end = len(l.m) - 1
            l.addROI(roi_start, roi_end, 'Snaith')
      
          if (args.mppt > 0):
            # maximum power point tracker
            print("Starting maximum power point tracker", file=sys.stderr, flush=True)
            if not(args.snaith or args.sweep):
              print("Warning: doing max power point tracking without prior sweep")
              Vmpp = 0.7
              dV = l.Voc / 101
              iMax = 0.04
            else:
              # find mpp from the previous sweep
              v = numpy.array([e[0] for e in sv])
              i = numpy.array([e[1] for e in sv])  
              p = v*i*-1
              maxIndex = numpy.argmax(p)
              Vmpp = v[maxIndex]
              iMax = compliance
      
              # use previous voltage step
              dV = sm.dV
      
            # switch to fixed DC mode
            sm.setupDC(sourceVoltage=True, compliance=iMax, setPoint=Vmpp, senseRange=iMax)
      
            # set exploration limits
            dAngleMax = 5 #[exploration degrees] (plus and minus)
      
            mpptCyclesRemaining = args.mppt
            
            q = deque()
      
            while (True):
              exploring = 0
              print("Teleporting to Mpp...", file=sys.stderr, flush=True)
              sm.setOutput(Vmpp)
              # dwell at Vmpp while measuring current
              print("Dwelling @ Mpp for {:} seconds...".format(args.t_dwell), file=sys.stderr, flush=True)
              # q = sm.measureUntil(t_dwell=args.t_dwell, cb=mpptCB)  #
              q = sm.measureUntil(t_dwell=args.t_dwell)
              qa = numpy.array([tuple(s) for s in q], dtype=l.measurement_datatype)
              l.m = numpy.append(l.m, qa)
      
              [Vmpp, Impp, now, status] = q.pop() # get the most recent entry
              if mpptCyclesRemaining == 0:
                print('Mppt final max power was {:0.4f} mW @ {:0.2f} mV'.format(Vmpp*Impp*1000*-1, Vmpp*1000))
                break
      
              print("Exploring for new Mpp...", file=sys.stderr, flush=True)
              exploring = 1
              i_explore = numpy.array(Impp)
              v_explore = numpy.array(Vmpp)
      
              angleMpp = numpy.rad2deg(numpy.arctan(Impp/Vmpp*l.Voc/l.Isc))
              print('MPP ANGLE = {:}'.format(angleMpp))
              v_set = Vmpp
              edgeANotTouched = True
              edgeBNotTouched = True
              while (edgeANotTouched or edgeBNotTouched):
                v_set = v_set + dV
                sm.write(':source:voltage {0:0.6f}'.format(v_set))
                measurement = sm.measure()
                [v, i, tx, status] = measurement
                dt = numpy.array([tuple(measurement)], dtype=l.measurement_datatype)
                l.m = numpy.append(l.m, qa)
      
                i_explore = numpy.append(i_explore, i)
                v_explore = numpy.append(v_explore, v)
                thisAngle = numpy.rad2deg(numpy.arctan(i/v*l.Voc/l.Isc))
                dAngle = thisAngle - angleMpp
                print("dAngle={:}, edgeANotTouched={:}, edgeBNotTouched={:}".format(dAngle, edgeANotTouched, edgeBNotTouched))
                if ((dAngle > dAngleMax) or (v_set >= l.Voc)) and ( edgeANotTouched ): # twords Voc edge
                  edgeANotTouched = False
                  if (v_set >= l.Voc):
                    because = 'Voc reached'
                  else:
                    because = 'angle limit reached'
                  if edgeBNotTouched:
                    print("Bouncing off Edge A because {:}".format(because), file=sys.stderr, flush=True)
                    dV = dV * -1 # switch our voltage walking direction
                  else:
                    print("Second edge (A) reached because {:}".format(because), file=sys.stderr, flush=True)
                if ((-dAngle > dAngleMax) or (v_set <= 0)) and ( edgeBNotTouched ): # towards Isc (V=0) edge
                  edgeBNotTouched = False
                  if (v_set <= 0):
                    because = 'V=0 reached'
                  else:
                    because = 'angle limit reached'
                  if edgeANotTouched:
                    print("Bouncing off Edge B because {:}".format(because), file=sys.stderr, flush=True)
                    dV = dV * -1 # switch our voltage walking direction
                  else:
                    print("Second edge (B) reached because {:}".format(because), file=sys.stderr, flush=True)
      
      
              print("Done exploring.", file=sys.stderr, flush=True)
      
              # find the powers for the values we just explored
              p_explore = v_explore * i_explore * -1
              maxIndex = numpy.argmax(p_explore)
              Vmpp = v_explore[maxIndex]
              Impp = i_explore[maxIndex]
      
              print("New Mpp found: {:.6f} mW @ {:.6f} V".format(p_explore[maxIndex]*1000, Vmpp), file=sys.stderr, flush=True)
      
              dFromLastMppAngle = numpy.rad2deg(numpy.arctan(Impp/Vmpp*l.Voc/l.Isc)) - angleMpp
      
              print("That's: {:.6f} degrees from the previous Mpp.".format(dFromLastMppAngle), file=sys.stderr, flush=True)
              mpptCyclesRemaining =  mpptCyclesRemaining - 1
      
      
          l.pixelComplete()
      l.runDone()
    sm.outOn(on=False)
    print("Program complete.")
        
  def get_args(self):
    """Get CLI arguments and options"""
    parser = argparse.ArgumentParser(description='Automated solar cell IV curve collector using a Keithley 24XX sourcemeter. Data is written to HDF5 files and human readable messages are written to stderr.')
    
    parser.add_argument('operator', type=str, help='Name of operator')
    parser.add_argument('--destination', help="Save output files here. '__tmp__' will use a system default temporary directory", type=self.is_dir, action=self.FullPaths)
  
    measure = parser.add_argument_group('optional arguments for measurement configuration')
    measure.add_argument("--pixel_address", default='0x01', type=str, action=self.RecordPref, help="Hex value to specify an enabled pixel bitmask (must start with 0x...)")
    measure.add_argument("--sweep", type=self.str2bool, default=False, action=self.RecordPref, const = True, help="Do an I-V sweep from Voc --> Jsc")
    measure.add_argument('--snaith', type=self.str2bool, default=False, action=self.RecordPref, const = True, help="Do an I-V sweep from Jsc --> Voc")
    measure.add_argument('--t-prebias', type=float, action=self.RecordPref, default=10, help="Number of seconds to sit at initial voltage value before doing sweep")
    measure.add_argument('--area', type=float, action=self.RecordPref, default=1.0, help="Specify device area in cm^2")
    measure.add_argument('--mppt', type=int, action=self.RecordPref, default=0, help="Do maximum power point tracking for this many cycles")
    measure.add_argument("--t-dwell", type=float, action=self.RecordPref, default=15, help="Total number of seconds for the dwell mppt phase(s)")  
    
    setup = parser.add_argument_group('optional arguments for setup configuration')
    setup.add_argument("--relay-ip", type=str, action=self.RecordPref, default='10.42.0.1', help="IP address of the WaveLabs relay server (set to 0 for direct WaveLabs connection)")  
    setup.add_argument('--wavelabs', type=self.str2bool, default=False, action=self.RecordPref, help="WaveLabs LED solar sim is present")
    setup.add_argument("--rear", type=self.str2bool, default=True, action=self.RecordPref, help="Use the rear terminals")
    setup.add_argument("--four-wire", type=self.str2bool, default=True, action=self.RecordPref, help="Use four wire mode (the defalt)")
    setup.add_argument("--current-compliance-override", type=float, help="Override current compliance value used diring I-V scans")
    setup.add_argument("--scan-low-override", type=float, help="Override more negative scan voltage value")
    setup.add_argument("--scan-high-override", type=float, help="Override more positive scan voltage value")
    setup.add_argument("--scan-points", type=int, action=self.RecordPref, default = 101, help="Number of measurement points in I-V curve")
    setup.add_argument("--scan-nplc", type=float, action=self.RecordPref, default = 1, help="Sourcemeter NPLC setting to use during I-V scan")  
    setup.add_argument("--terminator", type=str, action=self.RecordPref, default='0A', help="Instrument comms read & write terminator (enter in hex)")
    setup.add_argument("--baud", type=int, action=self.RecordPref, default=57600, help="Instrument serial comms baud rate")
    setup.add_argument("--port", type=int, action=self.RecordPref, default=23, help="Port to connect to switch hardware")
    setup.add_argument("--address", default='GPIB0::24::INSTR', type=str, action=self.RecordPref, help="VISA resource name for sourcemeter")
    setup.add_argument("--switch-address", type=str, default='10.42.0.54', action=self.RecordPref, help="IP address for PCB")
    setup.add_argument("--diode-calibration", type=int, nargs=2, action=self.RecordPref, default=(1,1), help="Calibration ADC counts for diodes D1 and D2 that correspond to 1 sun")
    setup.add_argument('--ignore-diodes', default=False, action='store_true', help="Assume 1.0 sun illumination")
    setup.add_argument('--visa-lib', type=str, action=self.RecordPref, default='@py', help="Path to visa library in case pyvisa can't find it, try C:\\Windows\\system32\\visa64.dll")
    
    testing = parser.add_argument_group('optional arguments for debugging/testing')
    testing.add_argument('--dummy', default=False, action='store_true', help="Run in dummy mode (doesn't need sourcemeter, generates simulated device data)")
    testing.add_argument("--scan", default=False, action='store_true', help="Scan for obvious VISA resource names, print them and exit")
    testing.add_argument('--test-hardware', default=False, action='store_true', help="Exercises all the hardware, used to check for and debug issues")
    # parser.add_argument('--file', type=str, action=self.RecordPref, help="Write output data stream to this file in addition to stdout.")
  
    return parser.parse_args()
      
  def buildQ(self, pixel_address_string):
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
          mask =  128 >> i
          if (byte & mask):
            q.append(substrate+str(i+1))
    else:
      q.append(pixel_address_string)
  
    return q
      
  
  def is_dir(self, dirname):
    """Checks if a path is an actual directory"""
    if (not os.path.isdir(dirname)) and dirname != '__tmp__':
      msg = "{0} is not a directory".format(dirname)
      raise argparse.ArgumentTypeError(msg)
    else:
      return dirname
    
  def str2bool(self, v):
    return bool(distutils.util.strtobool(v))
  
def main():
  cli = cli()
  cli.run()
