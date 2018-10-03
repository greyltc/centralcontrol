#!/usr/bin/env python3

import socketserver
import xml.etree.cElementTree as ET
import time

class wavelabs:
  """interface to the wavelabs LED solar simulator"""
  iseq = 0  # sequence number for comms with wavelabs software
  default_recipe = 'am1_5_1_sun'
  wavelabs_port = 3334
  relay_server_ip = 'localhost'
  relay_server_port = 3335

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

  def __init__(self, listen_ip = "0.0.0.0", listen_port = wavelabs_port, relay_host = relay_server_ip, relay_port = relay_server_port):
    self.host = listen_ip
    self.port = listen_port
    self._relay_host = relay_host
    self._relay_port = relay_port
    
  def __del__(self):
    try:
      self.server.close()
    except:
      pass
    try:
      self.sock_file.close()
      self.connection.close()
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
    self.connection.connect((self._relay_host, self._relay_port))
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

  def startRecipe(self):
    """starts the last activated recipe"""
    root = ET.Element("WLRC")
    ET.SubElement(root, 'StartRecipe', iSeq=str(self.iseq), sAutomationID = 'justtext')
    self.iseq =  self.iseq + 1
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: Recipe could not be started")

  def cancelRecipe(self):
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
  wl = wavelabs()
  #wl.startServer()
  #wl.awaitConnection()
  # or
  wl.connectToRelay()
  wl.activateRecipe(wl.default_recipe)
  print('Light turns on in...')
  time.sleep(1)
  print('3...')
  time.sleep(1)
  print('2...')
  time.sleep(1)
  print('1...')
  time.sleep(1)
  print('Now!')
  wl.startRecipe()
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
  wl.cancelRecipe()
  wl.server.close()
