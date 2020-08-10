#!/usr/bin/env python3

import socketserver
import xml.etree.cElementTree as ET
import time

class wavelabs:
  """interface to the wavelabs LED solar simulator"""
  iseq = 0  # sequence number for comms with wavelabs software

  class XMLHandler:
    """
    Class for handling the XML responses from the wavelabs software
    """
    def __init__(self):
      self.done_parsing = False
      self.error = None
      self.error_message = None
      self.run_ID = None
      # these are for GetDataSeries[]
      self.this_series = None
      self.type = []
      self.unit = []
      self.name = []
      self.series = {}

    def start(self, tag, attrib):
      if 'iEC' in attrib:
        self.error = int(attrib['iEC'])
      if 'sError' in attrib:
        self.error_message = attrib['sError']
      if 'sRunID' in attrib:
        self.run_ID = attrib['sRunID']
      if 'sVal' in attrib:
        self.paramVal = attrib['sVal']
      if 'sName' in attrib:
        self.name.append(attrib['sName'])
      if 'sUnit' in attrib:
        self.unit.append(attrib['sUnit'])
      if 'sType' in attrib:
        self.type.append(attrib['sType'])
      if tag == 'DataSeries':
        self.this_series = attrib['sName']

    def end(self, tag):
      if tag == 'WLRC':
        self.done_parsing = True
      if tag == 'DataSeries':
        series = self.series[self.this_series].split(';')
        self.series[self.this_series] = [float(x) for x in series]
        self.this_series = None

    def data(self, data):
      if self.this_series in self.series:
        self.series[self.this_series] = self.series[self.this_series] + data
      else:
        self.series[self.this_series] = data

    def close(self):
      pass  

  def __init__(self, host="0.0.0.0", port=3334, relay=False, connection_timeout=10, default_recipe='am1_5_1_sun'):
    """
    sets up the wavelabs object
    address is a string of the format:
    wavelabs://listen_ip:listen_port (should probably be wavelabs://0.0.0.0:3334)
    or
    wavelabs-relay://host_ip:host_port (should probably be wavelabs-relay://localhost:3335)
    
    """
    self.relay = relay
    self.host = host
    self.port = port
    self.def_port_non_relay = 3334
    self.def_port_relay = 3335
    self.timeout = connection_timeout
    self.default_recipe = default_recipe
    
  def __del__(self):
    try:
      self.sock_file.close()
    except:
      pass
    
    try:
      self.connection.close()
    except:
      pass

    try:
      self.server.server_close()
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
    self.server.timeout = self.timeout/10  # INNER timeout when waiting for the wavelabs software to connect
    self.server.allow_reuse_address = True
    self.server.server_bind()
    self.server.server_activate()

  #  0 is success
  # -1 is timeout
  # something else is not set recipe error
  # -3 is programming error
  def connect(self):
    """
    generic connect method, does what's appropriate for getting comms up based on self.relay, returns 0 on successful connection
    (aka successful setting of )
    """
    ret = -3
    if self.relay == False:
      if self.port is None:
        self.port = self.def_port_non_relay
      self.startServer()
      ret = self.awaitConnection()
      if ret == 0:
        ret = self.activateRecipe(self.default_recipe)
    else: # relay
      if self.port is None:
        self.port = self.def_port_relay
      self.connectToRelay()
      ret = self.activateRecipe(self.default_recipe)
    return (ret)


  #  0 is success
  # -1 is timeout
  # -3 is programming error
  def awaitConnection(self):
    """returns once the wavelabs program has connected"""
    t0 = time.time()
    timeout = self.timeout
    ret = -3
    requestNotVerified = True
    time_left = timeout - (time.time() - t0)
    old_tout = self.server.socket.gettimeout()
    self.server.socket.settimeout(self.timeout/10)
    while requestNotVerified and (time_left > 0):
      try:
        request, client_address = self.server.get_request()
        if self.server.verify_request(request, client_address):
          self.sock_file = request.makefile(mode="rwb")
          self.connection = request
          requestNotVerified = False
          ret = 0
      except:
        pass
      time_left = timeout - (time.time() - t0)
    if time_left <= 0:
      ret = -1
    self.server.socket.settimeout(old_tout)
    return (ret)
        
  def connectToRelay(self):
    """forms connection to the relay server"""
    self.connection = socketserver.socket.socket(socketserver.socket.AF_INET, socketserver.socket.SOCK_STREAM)
    self.connection.connect((self.host, int(self.port)))
    self.sock_file = self.connection.makefile(mode="rwb")

  def startFreeFloat(self, time = 0, intensity_relative = 100, intensity_sensor = 0, channel_nums = ['8'], channel_values=[50.0]):
    """starts/modifies/ends a free-float run"""
    root = ET.Element("WLRC")
    se = ET.SubElement(root, 'StartFreeFloat', iSeq=str(self.iseq), fTime = str(time), fIntensityRelative = str(intensity_relative), fIntensitySensor=str(intensity_sensor))
    self.iseq =  self.iseq + 1
    num_chans = len(channel_nums)
    for i in range(num_chans):
      ET.SubElement(se, 'Channel', iCh=str(channel_nums[i]), fInt=str(channel_values[i]))
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: FreeFloat command could not be handled")

  def activateRecipe(self, recipe_name=None):
    """activate a solar sim recipe by name"""
    if recipe_name is None:
      recipe_name = self.default_recipe
    root = ET.Element("WLRC")
    ET.SubElement(root, 'ActivateRecipe', iSeq=str(self.iseq), sRecipe = recipe_name)
    self.iseq =  self.iseq + 1
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: Recipe '{:}' could not be activated, check that it exists".format(recipe_name))
    return response.error
      
  def waitForResultAvailable(self, timeout=10000, run_ID=None):
    """wait for result from a recipe to be available"""
    root = ET.Element("WLRC")
    if run_ID == None:
      ET.SubElement(root, 'WaitForResultAvailable', iSeq=str(self.iseq), fTimeout = str(timeout))
    else:
      ET.SubElement(root, 'WaitForResultAvailable', iSeq=str(self.iseq), fTimeout = str(timeout), sRunID = run_ID)
    self.iseq =  self.iseq + 1
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: Failed to wait for result")

  def waitForRunFinished(self, timeout=10000, run_ID=None):
    """wait for the current run to finish"""
    root = ET.Element("WLRC")
    if run_ID == None:
      ET.SubElement(root, 'WaitForRunFinished', iSeq=str(self.iseq), fTimeout = str(timeout))
    else:
      ET.SubElement(root, 'WaitForRunFinished', iSeq=str(self.iseq), fTimeout = str(timeout), sRunID = run_ID)
    self.iseq =  self.iseq + 1
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: Failed to wait for run finish")
      
  def getRecipeParam(self, recipe_name=None, step=1, device="Light", param="Intensity"):
    if recipe_name is None:
      recipe_name = self.default_recipe
    ret = None
    root = ET.Element("WLRC")
    ET.SubElement(root, 'GetRecipeParam', iSeq=str(self.iseq), sRecipe = recipe_name, iStep = str(step), sDevice=device, sParam=param)
    self.iseq =  self.iseq + 1
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: Failed to get recipe parameter")
    else:
      ret = response.paramVal
    return ret

  def getDataSeries(self, step=1, device="LE", curve_name="Irradiance-Wavelength", attributes="raw", run_ID=None):
    """returns a data series from SinusGUI"""
    ret = None
    root = ET.Element("WLRC")
    if run_ID == None:
      ET.SubElement(root, 'GetDataSeries', iSeq=str(self.iseq), iStep = str(step), sDevice=device, sCurveName=curve_name, sAttributes=attributes)
    else:
      ET.SubElement(root, 'GetDataSeries', iSeq=str(self.iseq), iStep = str(step), sDevice=device, sCurveName=curve_name, sAttributes=attributes, sRunID=run_ID)
    self.iseq =  self.iseq + 1
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: Failed to get recipe parameter")
    else:
      ret = []
      n_series = len(response.name) # number of data series we got
      for i in range(n_series):
        series = {}
        series['name'] = response.name
        series['unit'] = response.unit
        series['type'] = response.type
        series['data'] = response.series
        ret.append(series)
    return ret
  
  def setRecipeParam(self, recipe_name=None, step=1, device="Light", param="Intensity", value=100.0):
    if recipe_name is None:
      recipe_name = self.default_recipe
    root = ET.Element("WLRC")
    ET.SubElement(root, 'SetRecipeParam', iSeq=str(self.iseq), sRecipe=recipe_name, iStep=str(step), sDevice=device, sParam=param, sVal=str(value))
    self.iseq =  self.iseq + 1
    tree = ET.ElementTree(root)
    tree.write(self.sock_file)
    response = self.recvXML()
    if response.error != 0:
      print("ERROR: Failed to set recipe parameter")
    else:
      self.activateRecipe(recipe_name=recipe_name)

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
      runID = None
    else:
      runID = response.run_ID
    return runID

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

  def get_spectrum(self):
    x = []
    y = []
    old_duration = None
    try:
      old_duration = int(self.getRecipeParam(param="Duration"))
      self.setRecipeParam(param="Duration", value=1000)
      run_ID = self.on()
      self.waitForRunFinished(run_ID = run_ID)
      self.waitForResultAvailable(run_ID = run_ID)
      spectra = self.getDataSeries(run_ID=run_ID)
      self.setRecipeParam(param="Duration", value=old_duration)
      spectrum = spectra[0]
      x = spectrum['data']['Wavelenght']
      y = spectrum['data']['Irradiance']
    except:
      if old_duration is not None:
        try:
          self.setRecipeParam(param="Duration", value=old_duration)
        except:
          pass
    return (x,y)

if __name__ == "__main__":
  #import matplotlib.pyplot as plt
  import pandas as pd
  wl = wavelabs(host='0.0.0.0', port=3334, default_recipe='AM1.5G', relay=False)  # for direct connection
  #wl = wavelabs(host='127.0.0.1', port=3335, relay=True, default_recipe='am1_5_1_sun')  #  for comms via relay
  print("Connecting to light engine...")
  wl.connect()
  old_intensity = wl.getRecipeParam(param="Intensity")
  old_duration = wl.getRecipeParam(param="Duration")
  new_intensity = 100.0
  new_duration = 5 # in seconds
  if new_duration < 3:
    raise(ValueError("Pick a new duration larger than 3"))
  wl.setRecipeParam(param="Duration", value=new_duration*1000)
  wl.setRecipeParam(param="Intensity", value=new_intensity)
  
  duration = wl.getRecipeParam(param="Duration")
  intensity = wl.getRecipeParam(param="Intensity") 
  print("Recipe Duration = {:} [s]".format(float(duration)/1000))
  print("Recipe Intensity = {:} [%]".format(intensity))  
  
  print('Light turns on in...')
  time.sleep(1)
  print('3...')
  time.sleep(1)
  print('2...')
  time.sleep(1)
  print('1...')
  time.sleep(1)
  print('Now!')
  run_ID = wl.on()
  print('Run ID: {:}'.format(run_ID))
  print('Light turns off in {:} [s]'.format(new_duration))
  time.sleep(new_duration-3)
  print('3...')
  time.sleep(1)
  print('2...')
  time.sleep(1)
  print('1...')
  time.sleep(1)
  print('Now!')
  wl.waitForRunFinished(run_ID = run_ID)
  wl.waitForResultAvailable(run_ID = run_ID)
  spectra = wl.getDataSeries(run_ID=run_ID)
  spectrum = spectra[0]
  x = spectrum['data']['Wavelenght']
  y = spectrum['data']['Irradiance']
  #plt.plot(x,y)
  #plt.ylabel('Irradiance')
  #plt.xlabel('Wavelength [nm]')
  #plt.grid(True)
  #plt.show()

  #wl.off()
  #wl.activateRecipe()
  wl.setRecipeParam(param="Intensity", value=old_intensity)
  wl.setRecipeParam(param="Duration", value=old_duration)
  
  duration = wl.getRecipeParam(param="Duration")
  intensity = wl.getRecipeParam(param="Intensity")
  print("Recipe Duration = {:} [s]".format(float(duration)/1000))
  print("Recipe Intensity = {:} [%]".format(intensity))

  print("Now we do the Christo Disco!")
  chan_names = ['all']
  chan_values = [0.0]
  disco_time = 10000 # [ms]
  wl.startFreeFloat(time = disco_time, channel_nums = chan_names, channel_values = chan_values)
  n_chans = 21
  disco_sleep = disco_time/n_chans
  disco_val = 75
  chan_names = [str(x) for x in range(1,n_chans+1)]
  for i in range(n_chans):
    print('{:}% on Channel {:}'.format(disco_val, chan_names[i]))
    chan_values = [0]*n_chans
    chan_values[i] = disco_val
    wl.startFreeFloat(time = disco_time, channel_nums=chan_names, channel_values=chan_values)
    time.sleep(disco_sleep/1000)
  wl.startFreeFloat() # stop freefloat
