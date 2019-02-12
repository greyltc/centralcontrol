#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# written by grey@mutovis.com

from mutovis_control import fabric

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
  ftp_host = None  # for archival data backup
  ftp_path = None  # for archival data backup
  
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
        
    if 'FTP' in config:
      self.ftp_host = config['FTP']['host']  
      self.ftp_path = config['FTP']['path']  # this needs to end in /, example: "/drop/"

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
    l = fabric(saveDir = args.destination, ignore_diodes=args.ignore_diodes, diode_calibration=args.diode_calibration, ftp =(self.ftp_host, self.ftp_path))
    self.l = l
    
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
          
          if type(args.current_compliance_override) == float:
            compliance = args.current_compliance_override
          else:
            compliance = l.compliance_guess  # we have to just guess what the current complaince here
            # TODO: probably need the user to tell us when it's a dark scan to get the sensativity we need in that case
          l.mppt.current_compliance = compliance
            
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
    
            message = 'Sweeping voltage from {:.0f} mV to {:.0f} mV'.format(start*1000, end*1000)
            sv = l.sweep(sourceVoltage=True, compliance=compliance, senseRange='f', nPoints=args.scan_points, start=start, end=end, NPLC=args.scan_nplc, message=message)
            l.registerMeasurements(sv, 'Sweep')
            
            (Pmax, Vmpp, Impp, maxIndex) = l.mppt.which_max_power(sv)
            l.mppt.Vmpp = Vmpp
            
            if type(args.current_compliance_override) == float:
              compliance = args.current_compliance_override
            else:
              compliance = abs(sv[-1][1] * 2)  # take the last measurement*2 to be our compliance limit
            l.mppt.current_compliance = compliance
    
          # steady state Isc measured here
          iscs = l.steadyState(t_dwell=args.t_prebias, NPLC = 10, sourceVoltage=True, compliance=compliance, senseRange ='a', setPoint=0)
          l.registerMeasurements(iscs, 'I_sc dwell')
    
          l.Isc = iscs[-1][1]  # take the last measurement to be Isc
          l.mppt.Isc = l.Isc
          
          if type(args.current_compliance_override) == float:
            compliance = args.current_compliance_override
          else:
            # if the measured steady state Isc was below 5 microamps, set the compliance to 10uA (this is probaby a dark curve)
            # we don't need the accuracy of the lowest current sense range (I think) and we'd rather have the compliance headroom
            # otherwise, set it to be 2x of Isc            
            if abs(l.Isc) < 0.000005:
              compliance = 0.00001
            else:
              compliance = abs(l.Isc * 2)          
          l.mppt.current_compliance = compliance
      
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
      
            sv = l.sweep(sourceVoltage=True, senseRange='f', compliance=compliance, nPoints=args.scan_points, start=start, end=end, NPLC=args.scan_nplc, message=message)
            l.registerMeasurements(sv, 'Snaith')
            (Pmax, Vmpp, Impp, maxIndex) = l.mppt.which_max_power(sv)
            l.mppt.Vmpp = Vmpp
      
          if (args.mppt > 0):
            message = 'Tracking maximum power point for {:} seconds'.format(args.mppt)
            l.track_max_power(args.mppt, message)

          l.pixelComplete()
      l.runDone()
    l.sm.outOn(on=False)
    print("Program complete.")
        
  def get_args(self):
    """Get CLI arguments and options"""
    parser = argparse.ArgumentParser(description='Automated solar cell IV curve collector using a Keithley 24XX sourcemeter. Data is written to HDF5 files and human readable messages are written to stderr.')
    
    parser.add_argument('operator', type=str, help='Name of operator')
    parser.add_argument('--destination', help="Save output files here. '__tmp__' will use a system default temporary directory", type=self.is_dir, action=self.FullPaths)
  
    measure = parser.add_argument_group('optional arguments for measurement configuration')
    measure.add_argument("--pixel_address", default='0x80', type=str, action=self.RecordPref, help='Hex value to specify an enabled pixel bitmask or individual pixel addresses "0xC0 == A1A2"')
    measure.add_argument("--sweep", type=self.str2bool, default=False, action=self.RecordPref, const = True, help="Do an I-V sweep from Voc --> Isc")
    measure.add_argument('--snaith', type=self.str2bool, default=False, action=self.RecordPref, const = True, help="Do an I-V sweep from Isc --> Voc")
    measure.add_argument('--t-prebias', type=float, action=self.RecordPref, default=10, help="Number of seconds to measure to find steady state Voc and Isc")
    measure.add_argument('--area', type=float, action=self.RecordPref, default=1.0, help="Specify device area in cm^2")
    measure.add_argument('--mppt', type=int, action=self.RecordPref, default=30, help="Do maximum power point tracking for this many seconds")
    
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
      pixels = [pixel_address_string[i:i+2] for i in range(0, len(pixel_address_string), 2)]
      for pixel in pixels:
        pixel_in_q = False
        if len(pixel) == 2:
          pixel_int = int(pixel[1])
          if (pixel[0] in self.l.pcb.substratesConnected) and (pixel_int >= 1 and pixel_int <= 8):
            q.append(pixel)  #  only put good pixels in the que
            pixel_in_q = True
        if pixel_in_q == False:
            print("WARNING! Discarded bad pixel address: {:}".format(pixel))
  
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
