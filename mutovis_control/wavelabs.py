#!/usr/bin/env python3

import socketserver
import xml.etree.cElementTree as ET
import time

class wavelabs:
  """interface to the wavelabs LED solar simulator"""
  iseq = 0  # sequence number for comms with wavelabs software
  protocol = 'wavelabs'  # communication method for talking to the wavelabs light engine, wavelabs for direct, wavelabs-relay for relay
  default_recipe = 'am1_5_1_sun'
  port = 3334  # 3334 for direct connection, 3335 for through relay service
  host = '0.0.0.0'  # 0.0.0.0 for direct connection, localhost for through relay service

  class XMLHandler:
    """
    Class for handling the XML responses from the wavelabs software
    """
    def __init__(self):
      self.done_parsing = False
      self.error = None
      self.error_message = None
      self.run_ID = None

    def start(self, tag, attrib):
      if 'iEC' in attrib:
        self.error = int(attrib['iEC'])
      if 'sError' in attrib:
        self.error_message = attrib['sError']
      if 'sRunID' in attrib:
        self.run_ID = attrib['sRunID']

    def end(self, tag):
      if tag == 'WLRC':
        self.done_parsing = True

    def data(self, data):
      pass

    def close(self):
      pass  

  def __init__(self, address="wavelabs://0.0.0.0:3334"):
    """
    sets up the wavelabs object
    address is a string of the format:
    wavelabs://listen_ip:listen_port (should probably be wavelabs://0.0.0.0:3334)
    or
    wavelabs-relay://host_ip:host_port (should probably be wavelabs-relay://localhost:3335)
    
    """
    self.protocol, location = address.split('://')
    self.host, self.port = location.split(':')
    self.port = int(self.port)
    
  def __del__(self):
    try:
      self.sock_file.close()
      self.connection.close()
    except:
      pass
    
    try:
      self.server.close()
    except:
      pass    

  def recvXML(self):
    """reads xml object from socket"""
    target = self.XMLHandler()
    parser = ET.XMLParser(target=target)
    while not target.done_parsing:
      parser.feed(self.connection.recv(1024))
    parser.close()
    if target.error != 0:
      print("Got error number {:} from WaveLabs software: {:}".format(target.error, target.error_message))
    return target

  def startServer(self):
    """define a server which listens for the wevelabs software to connect"""
    self.iseq = 0

    self.server = socketserver.TCPServer((self.host, self.port), socketserver.StreamRequestHandler, bind_and_activate = False)
    self.server.timeout = None  # never timeout when waiting for the wavelabs software to connect
    self.server.allow_reuse_address = True
    self.server.server_bind()
    self.server.server_activate()
    
  def connect(self):
    """
    generic connect method, does what's appropriate for getting comms up based on self.protocol
    """
    if self.protocol == 'wavelabs':
      self.startServer()
      self.awaitConnection()
      self.activateRecipe(self.default_recipe)
    elif self.protocol == 'wavelabs-relay':
      self.connectToRelay()
      self.activateRecipe(self.default_recipe)
    else:
      print("WRNING: Got unexpected wavelabs comms protocol: {:}".format(self.protocol))

  def awaitConnection(self):
    """returns once the wavelabs program has connected"""
    requestNotVerified = True
    while requestNotVerified:
      request, client_address = self.server.get_request()
      if self.server.verify_request(request, client_address):
        self.sock_file = request.makefile(mode="rwb")
        self.connection = request
        requestNotVerified = False
        
  def connectToRelay(self):
    """forms connection to the relay server"""
    self.connection = socketserver.socket.socket(socketserver.socket.AF_INET, socketserver.socket.SOCK_STREAM)
    self.connection.connect((self.host, int(self.port)))
    self.sock_file = self.connection.makefile(mode="rwb")

  def activateRecipe(self, recipe_name=default_recipe):
    """activate a solar sim recipe by name"""
    root = ET.Element("WLRC")
    ET.SubElement(root, 'ActivateRecipe', iSeq=str(self.iseq), sRecipe = recipe_name)
    self.iseq =  self.iseq + 1
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: Recipe '{:}' could not be activated, check that it exists".format(recipe_name))

  def on(self):
    """starts the last activated recipe"""
    root = ET.Element("WLRC")
    ET.SubElement(root, 'StartRecipe', iSeq=str(self.iseq), sAutomationID = 'justtext')
    self.iseq =  self.iseq + 1
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: Recipe could not be started")

  def off(self):
    """cancel a currently running recipe"""
    root = ET.Element("WLRC")
    ET.SubElement(root, 'CancelRecipe', iSeq=str(self.iseq))
    self.iseq =  self.iseq + 1
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: Could not cancel recipe, maybe it's not running")

  def exitProgram(self):
    """closes the wavelabs solar sim program on the wavelabs PC"""
    root = ET.Element("WLRC")
    ET.SubElement(root, 'ExitProgram', iSeq=str(self.iseq))
    self.iseq =  self.iseq + 1
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: Could not exit WaveLabs program")     

if __name__ == "__main__":
  # wl = wavelabs('wavelabs://0.0.0.0:3334')  # for direct connection
  wl = wavelabs('wavelabs-relay://0.0.0.0:3335')  #  for comms via relay
  print('Light turns on in...')
  time.sleep(1)
  print('3...')
  time.sleep(1)
  print('2...')
  time.sleep(1)
  print('1...')
  time.sleep(1)
  print('Now!')
  wl.on()
  time.sleep(1)
  print('Light turns off in...')
  time.sleep(1)
  print('3...')
  time.sleep(1)
  print('2...')
  time.sleep(1)
  print('1...')
  time.sleep(1)
  print('Now!')
  wl.off()