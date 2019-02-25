#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# written by grey@mutovis.com

from mutovis_control import fabric

import sys
import argparse
import time
import os
import distutils.util

import appdirs
import configparser
import ast
import pathlib

import xmlrpc.client  # here's how we get measurement data out as it's collected

from collections import deque

# for updating prefrences
prefs = {} # TODO: figure out how to un-global this

class cli:
  """the command line interface"""
  appname = 'mutovis_control_software'
  config_section = 'PREFRENCES'
  prefs_file_name = 'prefs.ini'
  config_file_fullpath = appdirs.user_config_dir(appname) + os.path.sep + prefs_file_name
  
  layouts_file_name = 'layouts.ini'  # this file holds the device layout definitions
  system_layouts_file_fullpath = sys.prefix + os.path.sep + 'etc' + os.path.sep + layouts_file_name
  
  layouts_file_used = ''
  
  archive_address = None  # for archival data backup
  
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
    """
    gets command line arguments and handles preference file
    """
    self.args = self.get_args()
    
    # for saving config
    config_path = pathlib.Path(self.config_file_fullpath)
    config_path.parent.mkdir(parents = True, exist_ok = True)
    config = configparser.ConfigParser()
    config.read(self.config_file_fullpath)
    
    # take command line args and put them in to prefrences
    if self.config_section not in config:
      config[self.config_section] = prefs
    else:
      for key, val in prefs.items():
        config[self.config_section][key] = str(val)
    
    # save the prefrences file
    with open(self.config_file_fullpath, 'w') as configfile:
      config.write(configfile)
    
    # now read back the new prefs
    config.read(self.config_file_fullpath)
    
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
      elif type(self.args.__getattribute__(key)) == list or type(self.args.__getattribute__(key)) == tuple:
        v = config.get(self.config_section, key)
        self.args.__setattr__(key, ast.literal_eval(v))
      else:
        self.args.__setattr__(key, config.get(self.config_section, key))

    # use local layouts if it exists, otherwise use system layouts definition file
    self.layouts_file_used = os.getcwd() + os.path.sep + self.layouts_file_name
    if not os.path.exists(self.layouts_file_used):
      self.layouts_file_used = self.system_layouts_file_fullpath
      if not os.path.exists(self.layouts_file_used):
        raise ValueError("{:} must be in {:} or in the current working directory".format(self.layouts_file_name, system_layouts_file_fullpath))
    
    layouts_config = configparser.ConfigParser()
    layouts_config.read(self.layouts_file_used)
    self.layouts = {}
    for layout in layouts_config.sections():
      this_layout = dict(layouts_config[layout])
      this_layout['name'] = layout
      for key, value in this_layout.items():
        if value.startswith('['):
          this_layout[key] = ast.literal_eval(value)  #  turn lists into lists
      for key, value in this_layout.items():
        if key.startswith('pixel') and len(value) < 8:
          n_padding = 8 - len(value)
          this_layout[key] = value.append([0.0]*n_padding)  # pad with zeros up to 8 pixels
      index = int(this_layout['index'])
      del(this_layout['index'])
      self.layouts[index] = this_layout

    #  attach ARCHIVE settings for data archiving 
    if 'ARCHIVE' in config:
      self.archive_address = config['ARCHIVE']['address']  # an address string where to archive data to as we collect it, like "ftp://epozz:21/drop/"

    self.args.sm_terminator = bytearray.fromhex(self.args.sm_terminator).decode()
    
    if self.args.light_address.upper() == 'NONE':
      self.args.light_address = None
      
    if self.args.motion_address.upper() == 'NONE':
      self.args.motion_address = None
      
    if self.args.layout_index == []:
      self.args.layout_index = [None]
    
    # this puts each of the the experimental_parameters the user gave
    # into a dict with the keys being the parameter names and the values being
    # deques that we can pop() off the correct values as we go through substrates
    exps = {}
    for i, pset in enumerate(self.args.experimental_parameter):
      tionary = {}
      variable_name = pset[0]
      del(pset[0])
      pq = deque(pset)
      pq.reverse()
      exps[variable_name] = pq
      self.args.experimental_parameter[i] = pq
    self.args.experimental_parameter = exps
    
  def run(self):
    """
    Does the measurements
    """
    args = self.args
  
    # create the control entity
    l = fabric(saveDir = args.destination, archive_address=self.archive_address)
    self.l = l
    
    # connect update gui function to the gui server's "drop" function
    s = xmlrpc.client.ServerProxy(args.gui_address)
    try:
      server_methods = s.system.listMethods()
      if 'drop' in server_methods:
        l.update_gui = s.drop
    except:
      pass  # there's probably just no server gui running
    
    # connect to PCB and sourcemeter
    l.connect(dummy=args.dummy, visa_lib=args.visa_lib, visaAddress=args.sm_address, visaTerminator=args.sm_terminator, visaBaud=args.sm_baud, lightAddress=args.light_address, motionAddress=args.motion_address, pcbAddress=args.pcb_address, ignore_adapter_resistors=args.ignore_adapter_resistors)
    
    if args.dummy:
      args.pixel_address = 'A1'
    else:
      if args.rear == False:
        l.sm.setTerminals(front=True)
      if args.four_wire == False:
        l.sm.setWires(twoWire=True)
    
    # build up the queue of pixels to run through
    if args.pixel_address is not None:
      pixel_que = self.buildQ(args.pixel_address)
    else:
      pixel_que = []

    if args.test_hardware:
      if pixel_que is []:
        holders_to_test = l.pcb.substratesConnected
      else:
        #turn the address que into a string of substrates
        mash = ''
        for pix in pixel_que:
          mash = mash + pix[0][0]
        # delete the numbers
        # mash = mash.translate({48:None,49:None,50:None,51:None,52:None,53:None,54:None,55:None,56:None})
        holders_to_test = ''.join(sorted(set(mash))) # remove dupes
      l.hardwareTest(holders_to_test.upper())
    else:  # if we do the hardware test, don't then scan pixels
      #  do run setup things now like diode calibration and opening the data storage file
      if args.calibrate_diodes == True:
        diode_cal = True
      else:
        diode_cal = args.diode_calibration_values
      intensity = l.runSetup(args.operator, diode_cal, ignore_diodes=args.ignore_diodes, run_description=args.run_description)
      if args.calibrate_diodes == True:
        d1_cal = intensity[0]
        d2_cal = intensity[1]
        print('Setting present intensity diode readings to be used as future 1.0 sun refrence values: [{:}, {:}]'.format(d1_cal, d2_cal))
        # save the newly read diode calibraion values to the prefs file
        config = configparser.ConfigParser()
        config.read(self.config_file_fullpath)
        config[self.config_section]['diode_calibration_values'] = str([d1_cal, d2_cal])
        with open(self.config_file_fullpath, 'w') as configfile:
          config.write(configfile)

      if args.sweep or args.snaith or args.mppt > 0:
        last_substrate = None
        # scan through the pixels and do the requested measurements
        for pixel in pixel_que:
          substrate = pixel[0][0].upper()
          pix = pixel[0][1]
          print('\nOperating on substrate {:s}, pixel {:s}...'.format(substrate, pix))
          if last_substrate != substrate:  # we have a new substrate
            print('New substrate using "{:}" layout!'.format(pixel[3]))
            last_substrate = substrate
            variable_pairs = []
            for key, value in self.args.experimental_parameter.items():
              variable_pairs.append([key, value.pop()])

            substrate_ready = l.substrateSetup(position=substrate, variable_pairs=variable_pairs, layout_name=pixel[3])
      
          pixel_ready = l.pixelSetup(pixel, t_dwell_voc = args.t_prebias)  #  steady state Voc measured here
          if pixel_ready and substrate_ready:
            
            if type(args.current_compliance_override) == float:
              compliance = args.current_compliance_override
            else:
              compliance = l.compliance_guess  # we have to just guess what the current complaince should be here
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
              sv = l.sweep(sourceVoltage=True, compliance=compliance, senseRange='a', nPoints=args.scan_points, start=start, end=end, NPLC=args.scan_nplc, message=message)
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
            l.f[l.position+'/'+l.pixel].attrs['Isc'] = l.Isc 
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
    parser = argparse.ArgumentParser(description='Automated solar cell IV curve collector using a Keithley 24XX sourcemeter. Data is written to HDF5 files and human readable messages are written to stdout.')
    
    parser.add_argument('-o', '--operator', type=str, required=True, help='Name of operator')
    parser.add_argument('-r', '--run-description', type=str, required=True, help='Words describing the measurements about to be taken')
    parser.add_argument('-p', '--experimental-parameter', type=str, nargs='+', action='append', required=True, help="Space separated experimental parameter name and values. Multiple parameters can be specified by additional uses of '-p'. Use one value per substrate measured. The first item given here is taken to be the parameter name and the rest of the items are taken to be the values for each substrate. eg. '-p Thickness 2m 3m 4m' would attach a Thickness attribute with values 2m 3m and 4m to the first, second and third substrate measured in this run respectively.")
  
    measure = parser.add_argument_group('optional arguments for measurement configuration')
    measure.add_argument('-d', '--destination', help="Directory in which to save the output data, '__tmp__' will use a system default temporary directory", type=self.is_dir, action=self.FullPaths)    
    measure.add_argument('-a', "--pixel-address", default=None, type=str, help='Hexadecimal bit mask for enabled pixels, also takes letter-number pixel addresses "0xFC == A1A2A3A4A5A6"')
    measure.add_argument("--sweep", type=self.str2bool, default=True, action=self.RecordPref, const = True, help="Do an I-V sweep from Voc --> Isc")
    measure.add_argument('--snaith', type=self.str2bool, default=True, action=self.RecordPref, const = True, help="Do an I-V sweep from Isc --> Voc")
    measure.add_argument('--t-prebias', type=float, action=self.RecordPref, default=10.0, help="Number of seconds to measure to find steady state Voc and Isc")
    measure.add_argument('--mppt', type=float, action=self.RecordPref, default=37.0, help="Do maximum power point tracking for this many seconds")
    measure.add_argument('-i', '--layout-index', type=int, nargs='*', action=self.RecordPref, default=[], help="Substrate layout(s) to use for finding pixel areas, read from layouts.ini file in CWD or {:}".format(self.system_layouts_file_fullpath))
    measure.add_argument('--area', type=float, nargs='*', default=[], help="Override pixel areas taken from layout (given in cm^2)")
    
    setup = parser.add_argument_group('optional arguments for setup configuration')
    setup.add_argument("--ignore-adapter-resistors", type=self.str2bool, default=True, action=self.RecordPref, const = True, help="Don't consider the resistor value of adapter boards when determining device layouts")
    setup.add_argument("--light-address", type=str, action=self.RecordPref, default='wavelabs-relay://localhost:3335', help="protocol://hostname:port for communication with the solar simulator, 'none' for no light, 'wavelabs://0.0.0.0:3334' for starting a wavelabs server on port 3334")
    setup.add_argument("--motion-address", type=str, action=self.RecordPref, default='none', help="protocol://hostname:port for communication with the motion controller, 'none' for no motion, 'afms:///dev/ttyAMC0' for an Adafruit Arduino motor shield on /dev/ttyAMC0")
    setup.add_argument("--rear", type=self.str2bool, default=True, action=self.RecordPref, help="Use the rear terminals")
    setup.add_argument("--four-wire", type=self.str2bool, default=True, action=self.RecordPref, help="Use four wire mode (the default)")
    setup.add_argument("--current-compliance-override", type=float, help="Override current compliance value used during I-V scans")
    setup.add_argument("--scan-low-override", type=float, help="Override more negative scan voltage value")
    setup.add_argument("--scan-high-override", type=float, help="Override more positive scan voltage value")
    setup.add_argument("--scan-points", type=int, action=self.RecordPref, default = 101, help="Number of measurement points in I-V curve")
    setup.add_argument("--scan-nplc", type=float, action=self.RecordPref, default = 1, help="Sourcemeter NPLC setting to use during I-V scans and max power point tracking")  
    setup.add_argument("--sm-terminator", type=str, action=self.RecordPref, default='0A', help="Visa comms read & write terminator (enter in hex)")
    setup.add_argument("--sm-baud", type=int, action=self.RecordPref, default=57600, help="Visa serial comms baud rate")
    setup.add_argument("--sm-address", default='GPIB0::24::INSTR', type=str, action=self.RecordPref, help="VISA resource name for sourcemeter")
    setup.add_argument("--pcb-address", type=str, default='10.42.0.54:23', action=self.RecordPref, help="host:port for PCB comms")
    setup.add_argument("--calibrate-diodes", default=False, action='store_true', help="Read diode ADC counts now and store those as corresponding to 1.0 sun intensity")    
    setup.add_argument("--diode-calibration-values", type=int, nargs=2, action=self.RecordPref, default=(1,1), help="Calibration ADC counts for diodes D1 and D2 that correspond to 1.0 sun intensity")
    setup.add_argument('--ignore-diodes', default=False, action='store_true', help="Ignore intensity diode readings and assume 1.0 sun illumination")
    setup.add_argument('--visa-lib', type=str, action=self.RecordPref, default='@py', help="Path to visa library in case pyvisa can't find it, try C:\\Windows\\system32\\visa64.dll")
    setup.add_argument('--gui-address', type=str, default='http://127.0.0.1:51246', action=self.RecordPref, help='protocol://host:port for the gui server')
    
    testing = parser.add_argument_group('optional arguments for debugging/testing')
    testing.add_argument('--dummy', default=False, action='store_true', help="Run in dummy mode (doesn't need sourcemeter, generates simulated device data)")
    testing.add_argument("--scan", default=False, action='store_true', help="Scan for obvious VISA resource names, print them and exit")
    testing.add_argument('--test-hardware', default=False, action='store_true', help="Exercises all the hardware, used to check for and debug issues")
    
    args = parser.parse_args()
    
    return args
      
  def buildQ(self, pixel_address_string, areas=None):
    """
    Generates a queue containing pixels we'll run through.
    Each element of the queue is a tuple: (address_string, area, position, layout_name)
    address_string is a string like A1
    area is the pixel area in cm^2
    position is the mm location for the center of the pixel
    
    inputs are
    pixel_address_string, which can just be a list like A1A2B3...or a hex bitmask
    substrate_definitions, this is a list of dictionaries with keys: 'name', 'areas', 'positions'
    
    if pixel_address_string starts with 0x, decode it as a hex value where
    a 1 in a position means that pixel is enabled
    the leftmost byte here is for substrate A
    the leftmost bit is for pixel one
    """
    q = []
    if pixel_address_string[0:2] == '0x':
      bitmask = bytearray.fromhex(pixel_address_string[2:])
      for substrate_index, byte in enumerate(bitmask):
        substrate = chr(substrate_index+ord('A'))
        if (pixel[0] in self.l.pcb.substratesConnected): #  only put good pixels in the queue
          for i in range(8):
            mask =  128 >> i
            if (byte & mask):
              q.append(substrate+str(i+1))
        else:
          print("WARNING! Substrate {:} could not be found".format(substrate))
    else:
      pixels = [pixel_address_string[i:i+2] for i in range(0, len(pixel_address_string), 2)]
      for pixel in pixels:
        pixel_in_q = False
        if len(pixel) == 2:
          pixel_int = int(pixel[1])
          if (pixel[0] in self.l.pcb.substratesConnected) and (pixel_int >= 1 and pixel_int <= 8):
            q.append(pixel)  #  only put good pixels in the queue
            pixel_in_q = True
        if pixel_in_q == False:
            print("WARNING! Discarded bad pixel address: {:}".format(pixel))
    
    # now we have a list of pixel addresses, q
    ret = []
    if len(q) > 0:
      using_layouts = {}
      user_layouts = deque(self.args.layout_index)  # layout indicies given to us by the user
      substrates = [x[0] for x in q]
      substrates = sorted(set(substrates))
      n = len(substrates)  # we have this many substrates
      for key, val in self.args.experimental_parameter.items():
        p = len(val)  # we got this many values for the key variable
        if p != n:
          raise ValueError('{:} Values were given for experimental parameter "{:}", but we are measuring {:} substrate(s).'.format(p, key, n))
      for substrate in substrates:
        r_value = self.l.pcb.resistors[substrate]
        valid_layouts = {}
        for key, value in self.layouts.items():
          targets = value['adapterboardresistor']
          for target in targets:
            if fabric.isWithinPercent(target, r_value) or self.args.ignore_adapter_resistors or target == 0:
              valid_layouts[key] = value
              break
        user_layout = user_layouts[0]  # here's the layout the user selected for this substrate
        user_layouts.rotate(-1)  # rotate the deque
        if user_layout in valid_layouts:
          using_layouts[substrate] = valid_layouts[user_layout]
          this_layout = valid_layouts[user_layout]
        elif len(valid_layouts) == 1:
          using_layouts[substrate] = valid_layouts.popitem()[1]
        else:
          raise ValueError("Could not determine the layout for substrate {:}. Use the -i argument with one of the following values {:}".format(substrate, valid_layouts))

      
      user_areas = deque(self.args.area)  # device areas given to us by the user
      for pxad in q:
        this_substrate = pxad[0]
        this_pixel = int(pxad[1])
        area = using_layouts[this_substrate]['pixelareas'][this_pixel]
        
        # absolute position for this pixel
        position = self.l.me.substrate_centers[ord(this_substrate)-ord('A')] + using_layouts[this_substrate]['pixelpositions'][this_pixel]
        if len(user_areas) > 0:
          print("WARNING: Overriding pixel {:}'s area value with {:} cm^2".format(user_areas[0]))
          area = user_areas[0]  # here's the area the user selected for this pixel
          user_areas.rotate(-1)  # rotate the deque
        if area == 0:
          print("INFO: Skipping zero area pixel: {:}".format(pxad))
        else:
          final_element = (pxad, area, position, using_layouts[this_substrate]['name'])
          ret.append(final_element)
    
    return deque(ret)
      
  
  def is_dir(self, dirname):
    """Checks if a path is an actual directory"""
    if (not os.path.isdir(dirname)) and dirname != '__tmp__':
      msg = "{0} is not a directory".format(dirname)
      raise argparse.ArgumentTypeError(msg)
    else:
      return dirname
    
  def str2bool(self, v):
    return bool(distutils.util.strtobool(v))
  
if __name__ == "__main__":
  cli = cli()
  cli.run()

