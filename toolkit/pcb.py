import socket

class pcb:
  """
  Interface for talking to my control PCB
  """
  write_terminator = '\r'
  read_terminator = b'\r\n'
  prompt = '>>> '
  substrateList = 'HGFEDCBA'  # all the possible substrates
  substratesConnected = ''  # the ones we've detected
  
  def __init__(self, ipAddress, port=23):
    timeout = 10
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((ipAddress, port))
    s.settimeout(timeout)
    pcb.set_keepalive_linux(s) # let's try to keep our connection alive!
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
          self.substratesConnected = self.substratesConnected + substrate
          found = found + substrate
      print(found)

  def __del__(self):
    self.disconnect_all()
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
    win = False
    ready = False
    try:
        cmd = "s" + substrate + str(pixel)
        answer, ready = self.query(cmd)
    except:
        raise (ValueError, "Failure while talking to PCB")
      
    if ready:
      if answer == '':
        win = True
      else:
        print("WARNING: unable to set pixel with command, {:s}".format(cmd))
        print("Got message: {:s}".format(answer))
    else:
      raise (ValueError, "Comms are out of sync with the PCB")

    return win
  
  # returns string, bool
  # the string is the response
  # the bool tells us if the read completed successfully
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
  
  def getADCCounts(self, chan):
    counts = None
    ready = False
    try:
        cmd = "ADC" + str(chan)
        answer, ready = self.query(cmd)
    except:
        raise (ValueError, "Failure while talking to PCB")
      
    if ready:
      if answer.startswith('AIN'):
        counts = int(answer.split(' ')[1])
      else:
        print("WARNING: unable to set pixel with command, {:s}".format(cmd))
        print("Got message: {:s}".format(answer))
    else:
      raise (ValueError, "Comms are out of sync with the PCB")

    return counts    
  
  def disconnect_all(self):
    """ Opens all the switches
    """
    for substrate in self.substratesConnected:
      self.pix_picker(substrate, 0)
    
  def set_keepalive_linux(sock, after_idle_sec=1, interval_sec=3, max_fails=5):
    """Set TCP keepalive on an open socket.
  
    It activates after 1 second (after_idle_sec) of idleness,
    then sends a keepalive ping once every 3 seconds (interval_sec),
    and closes the connection after 5 failed ping (max_fails), or 15 seconds
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, after_idle_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, max_fails)
  
  def set_keepalive_osx(sock, after_idle_sec=1, interval_sec=3, max_fails=5):
    """Set TCP keepalive on an open socket.
  
    sends a keepalive ping once every 3 seconds (interval_sec)
    """
    # scraped from /usr/include, not exported by python's socket module
    TCP_KEEPALIVE = 0x10
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, TCP_KEEPALIVE, interval_sec)  