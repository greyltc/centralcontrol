#!/usr/bin/env python

from telnetlib import Telnet
import socket
import os


class pcb:
  """
  Interface for talking to my control PCB
  """
  write_terminator = '\r\n'
  #read_terminator = b'\r\n' # probably don't care. all whitespace gets stripped anyway
  prompt = b'>>> '
  
  substrateList = 'HGFEDCBA'  # all the possible substrates
  # TODO: this is out of date. with otter it's not one substrate per mux board anymore.
  # unsure of the implications, if any right now
  
  substratesConnected = ''  # the ones we've detected
  adapters = []  # list of tuples of adapter boards: (substrate_letter, resistor_value)

  class MyTelnet(Telnet):
    def read_response(self, timeout=None):
      found_prompt = False
      resp = self.read_until(pcb.prompt, timeout=None)
      if resp.endswith(pcb.prompt):
        found_prompt = True
      ret = resp.rstrip(pcb.prompt).decode().strip()
      if len(resp) == 0:
        ret = None  # nothing came back (likely a timeout)
      return ret, found_prompt

    def send_cmd(self, cmd):
      if not cmd.endswith(pcb.write_terminator.decode()):
        self.write(cmd.encode())
      else:
        self.write(cmd.encode()+pcb.write_terminator)
      self.sock.sendall()

  def __init__(self, address, ignore_adapter_resistors=True, timeout=socket._GLOBAL_DEFAULT_TIMEOUT):
    self.timeout = timeout # pcb has this many seconds to respond
    self.ignore_adapter_resistors = ignore_adapter_resistors

    addr_split = address.split(':')
    if len(addr_split) == 1:
      port = 23  # default port
      host = addr_split[0]
    else:
      host, port = address.split(':')

    self.host = host
    self.port = int(port)


  def __enter__(self):
    self.tn = self.MyTelnet(self.host, self.port, timeout=self.timeout)
    self.sf = self.tn.sock.makefile("rwb", buffering=0)

    if os.name != 'nt':
      pcb.set_keepalive_linux(self.tn.sock)  # let's try to keep our connection alive!

    welcome_message, win = self.tn.read_response()

    if not win:
      raise ValueError('Did not see welcome message from pcb')

    version = self.get('v')
    #print(f"Connected to control PCB running firmware version {version}")

    substrates = self.substrateSearch()
    resistors = {}  # dict of measured resistor values where the key is the associated substrate

    if substrates == 0x00:
      print('No multiplexer board detected.')
    else:
      found = "Found MUX board(s): "
      for i in range(len(self.substrateList)):
        substrate = self.substrateList[i]
        mask = 0x01 << (7-i)
        if (mask & substrates) != 0x00:
          self.substratesConnected = self.substratesConnected + substrate
          if self.ignore_adapter_resistors:
            resistors[substrate] = 0
          else:
            resistors[substrate] = self.get('d'+substrate)
          found = found + substrate
      #print(found)
    self.resistors = resistors
    return(self)

  def __exit__(self, type, value, traceback):
    try:
      self.disconnect_all()
    except:
      pass
    try:
      self.sf.close()
    except:
      pass
    try:
      self.tn.close()
    except:
      pass

  def substrateSearch(self):
    """Returns bitmask of connected MUX boards
    """
    substrates = self.substrateList
    found = 0x00
    win = False
    for i in range(len(substrates)):
      cmd = "c" + substrates[i]
      answer = self.get(cmd)
      if answer == "":  # empty answer means mux board found
        found |= 0x01 << (7-i)
    return found

  def pix_picker(self, substrate, pixel, suppressWarning=False):
    win = False
    ready = False
    retries = 5
    try_num = 0
    while try_num < retries:
      try:
        cmd = "s" + substrate + str(pixel)
        answer, ready = self.query(cmd)
      except:
        pass
      if ready:
        if answer == '':
          break
      retries += 1

    if ready:
      if answer == '':
        win = True
      else:
        print('WARNING: Got unexpected response form PCB to "{:s}": {:s}'.format(cmd, answer))
    else:
      raise (ValueError("Comms are out of sync with the PCB"))

    return win

  def write(self, cmd):
    if not cmd.endswith(self.write_terminator):
      cmd = cmd + self.write_terminator

    self.sf.write(cmd.encode())
    self.sf.flush()

  def query(self, query):
    self.write(query)
    return self.tn.read_response()


  def get(self, cmd):
    """
    sends cmd to the pcb and returns the relevant command response
    """
    ready = False
    ret = None

    # TODO: don't need to retry some commands that start with these letters
    # like eqe and stream
    retry_cmds = ['j','h', 'l', 's', 'r', 'c', 'e', 'g']
    super_retry_cmds = ['b']
    if cmd[0] in retry_cmds:
      tries_left = 5
    elif cmd[0] in super_retry_cmds: # very important to get through because this is e-stop
      tries_left = 5000
    else:
      tries_left = 1

    while tries_left > 0:
      try:
        answer, ready = self.query(cmd)
        if (ready == True) and ('ERROR' not in answer):
          break
      except:
        ready = False
        #raise (ValueError, "Failure while talking to PCB")
      tries_left -= 1

    if ready:
      # try to parse by question
      if cmd.startswith('g'):
        if answer.startswith('ERROR'):
          ret = None  # TODO: probably shouldn't just chuck this error...
        else:
          ret = answer
      elif cmd.startswith('l'):
        if answer.startswith('ERROR'):
          ret = None  # TODO: probably shouldn't just chuck this error...
        else:
          ret = int(answer)
      elif cmd.startswith('r'):
        if answer.startswith('ERROR'):
          ret = None  # TODO: probably shouldn't just chuck this error...
        else:
          ret = int(answer)
      elif cmd.startswith('h'):
        if answer.startswith('ERROR'):
          ret = None  # TODO: probably shouldn't just chuck this error...
        else:
          ret = answer
      elif cmd.startswith('j'):
        if answer.startswith('ERROR'):
          ret = None  # TODO: probably shouldn't just chuck this error...
        else:
          ret = answer
      else:  # not a question parsable command, attempt to parse by answer
        if answer.startswith('AIN'):
          ret = answer.split(' ')[1]
        elif answer.startswith('Board'):
          ret = int(answer.split(' ')[5])
        else:  # could not parse by either question or answer, fine. just return the result
          ret = answer
    else: # ready is False
      # TODO: should not raise here
      raise (ValueError("Comms are out of sync with the PCB"))

    return ret

  def getADCCounts(self, chan):
    """makes adc readings.
    chan can be 0-7 to directly read the corresponding adc channel
    """
    cmd = ""

    if (type(chan) == int):
      cmd = "ADC" + str(chan)

    return int(self.get(cmd))

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


# testing
if __name__ == "__main__":
  pcb_address = '10.42.0.239'
  with pcb(pcb_address, ignore_adapter_resistors=True) as p:
    print(f"Mux Check result = {p.get('c')}")
    print(f"Stage Check result = {p.get('e')}")
