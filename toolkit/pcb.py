import socket

class pcb:
  """
  Intertace for talking to my control PCB
  """
  write_terminator = '\r'
  read_terminator = b'\r\n'
  prompt = '>>> '
  substrateList = 'HGFEDCBA'
  
  def __init__(self, ipAddress, port=23):
    timeout = 0.5
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((ipAddress, port))
    s.settimeout(timeout)
    sf = s.makefile("rwb", buffering=0)

    self.s = s
    self.sf = sf
    
    self.write('') # check on switch
    answer, win = self.getResponse()
    
    if not win:
      raise ValueError('Got bad response from switch')
    
    substrates = self.substrateSearch()
    
    if substrates == 0x00:
      print('No multiplexer board detected.')
    else:
      found = "Found MUX board(s): "
      for i in range(len(self.substrateList)):
        substrate = self.substrateList[i]
        mask = 0x01 << (7-i)
        if (mask & substrates) != 0x00:
          found = found + substrate
      print(found)

  def __del__(self):
    self.disconnect()
    
  def substrateSearch(self):
    """Returns bitmask of connected MUX boards
    """
    substrates = self.substrateList
    found = 0x00
    win = False
    for i in range(len(substrates)):
      cmd = "c" + substrates[i]
      answer, win = self.query(cmd)
      if answer == "MUX OK":
        found |= 0x01 << (7-i)
    return found
    
  def disconnect(self):
    self.sf.close()
    try:
        self.s.shutdown(socket.SHUT_RDWR)
    except:
        pass
    self.s.close()
    
  def pix_picker(self, substrate, pixel, suppressWarning=False):
    sf = self.sf
    win = False
    try:
        cmd = "s" + substrate + str(pixel)
        answer, win = self.query(cmd)
    except:
        pass
    
    if (not win) and (not suppressWarning):
        print("WARNING: unable to set pixel with command, {:s}".format(cmd))

    return win
  
  def getResponse(self):
    sf = self.sf
    line = None
    win = False
    try:
      line = sf.readline()
      if line.endswith(self.read_terminator):
        line = line[:-len(self.read_terminator)].decode() # strip off the terminator and decode
      else:
        print("WARNING: Didn't find expected terminator during read")
      maybePrompt = sf.read(len(self.prompt))
      if maybePrompt.decode() == self.prompt:
        win = True
      else: # it's not the prompt, so let's finish the line
        theRest = sf.readline()
        line = maybePrompt + theRest
        if line.endswith(self.read_terminator):
          line = line[:-len(self.read_terminator)].decode() # strip off the terminator and decode
          maybePrompt = sf.read(len(self.prompt))
          if maybePrompt.decode() == self.prompt:
            win = True             
        else:
          print("WARNING: Didn't find expected terminator during read")
     
    except:
      pass
    return line, win
  
  def write(self, cmd):
    sf = self.sf
    if not cmd.endswith(self.write_terminator):
      cmd = cmd + self.write_terminator
    
    sf.write(cmd.encode())
    sf.flush()
  
  def query(self, query):
    self.write(query)
    return self.getResponse()
    
    