#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# written by grey@mutovis.com

import matplotlib.pyplot as plt
from control_gui import server
from collections import deque
import argparse
import numpy

class gui:
  """the graphical user interface"""
  # appname = 'mutovis_control_gui'
  # config_section = 'PREFRENCES'
  server = None
  args = None
  # a place to store the last 64 measurements we've done
  rois = deque([], 64)
  
  def __init__(self):
    self.args = self.get_args()
    self.server = server(self.args.server_listen_ip, self.args.server_listen_port)
    self.server.rpc_server.register_function(self.q_append, name='drop')  #
    # plt.ion()
    
  def __del__(self):
      self.server.stop_server()

  def run(self):
    self.server.run_server()
    
  def q_append(self, item):
    """
    puts an item into the roi queue
    """
    self.rois.append(item)
    
    print("Device area = {:}".format(item['area']))
    v = numpy.array(item['v'])
    i = numpy.array(item['i'])
    t = numpy.array(item['t'])
    s = numpy.array(item['s'])
    
    plt.figure()
    plt.plot(t, v, '.')
    plt.ylabel('Potential [Volts]')
    plt.xlabel('Time [Seconds]')
    plt.title(item['message'])
    plt.savefig("/tmp/"+item['message']+'_0')
    
    plt.figure()
    plt.plot(t, i, '.')
    plt.ylabel('Current [Amps]')
    plt.xlabel('Time [Seconds]')
    plt.title(item['message'])
    plt.savefig("/tmp/"+item['message']+'_1')
    
    plt.figure()
    plt.plot(v, i, '.')
    plt.ylabel('Current [Amps]')
    plt.xlabel('Potential [Volts]')
    plt.title(item['message'])
    plt.savefig("/tmp/"+item['message']+'_2')
    
    plt.figure()
    plt.plot(t, abs(v*i), '.')
    plt.ylabel('Power [Watts]')
    plt.xlabel('Time [Seconds]')
    plt.title(item['message'])
    plt.savefig("/tmp/"+item['message']+'_3')
    
    # plt.show()
    # plt.pause(0.1)
    return 0
        
  def get_args(self):
    """Get CLI arguments and options"""
    parser = argparse.ArgumentParser(description='Mutovis control GUI')

    setup = parser.add_argument_group('optional arguments')
    setup.add_argument("--server-listen-ip", type=str,  default='0.0.0.0', help="The GUI will listen on this interface")
    setup.add_argument("--server-listen-port", type=int, default=51246, help="The GUI will listen on this port")
  
    return parser.parse_args()
