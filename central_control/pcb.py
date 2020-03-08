#!/usr/bin/env python

import socket
import os


class pcb:
  """
  Interface for talking to my control PCB
  """
  write_terminator = '\r\n'
  read_terminator = b'\r\n'
  prompt = '>>> '
  substrateList = 'HGFEDCBA'  # all the possible substrates
  substratesConnected = ''  # the ones we've detected
  adapters = []  # list of tuples of adapter boards: (substrate_letter, resistor_value)

  def __init__(self, address, ignore_adapter_resistors=False):
    timeout = 10  # pcb has this many seconds to respond
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    addr_split = address.split(':')
    if len(addr_split) == 1:
        port = 23  # default port
        host = addr_split[0]
    else:
        host, port = address.split(':')
    s.settimeout(timeout)
    s.connect((socket.gethostbyname(host), int(port)))
    if os.name != 'nt':
      pcb.set_keepalive_linux(s)  # let's try to keep our connection alive!
    sf = s.makefile("rwb", buffering=0)

    self.s = s
    self.sf = sf

    welcome_message, win = self.getResponse()

    if not win:
      raise ValueError('Did not see welcome message from pcb')

    print(f"Connected to control PCB running firmware version {self.get('v')}")

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
          if ignore_adapter_resistors:
            resistors[substrate] = 0
          else:
            resistors[substrate] = self.get('d'+substrate)
          found = found + substrate
      print(found)
    self.resistors = resistors

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
      answer = self.get(cmd)
      if answer == "":  # empty answer means mux board found
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
        print('WARNING: Got unexpected response form PCB to "{:s}": {:s}'.format(cmd, answer))
    else:
      raise (ValueError, "Comms are out of sync with the PCB")

    return win


  # returns string, bool
  # the string is the response
  # the bool tells us if the read completed successfully
  def getResponse(self):
    sf = self.sf
    result = None
    found_prompt = False

    try:
      maybePrompt = sf.read(1) + sf.read(1) + sf.read(1) + sf.read(1)  # a prompt has length 4
      while found_prompt == False:
        if result is None:
          result = b""
        if maybePrompt.decode() == self.prompt:
          found_prompt = True
          break
        else:  # it's not the prompt, so let's keep reading
          theRest = sf.readline()
          result = result + maybePrompt + theRest
          maybePrompt = sf.read(1) + sf.read(1) + sf.read(1) + sf.read(1)  # a prompt has length 4
    except:
      pass
    if result is not None:
      result = result.decode().rstrip() # strip off the final terminator and decode
    return result, found_prompt

  def write(self, cmd):
    sf = self.sf
    if not cmd.endswith(self.write_terminator):
      cmd = cmd + self.write_terminator

    sf.write(cmd.encode())
    sf.flush()


  def query(self, query):
    self.write(query)
    return self.getResponse()


  def get(self, cmd):
    """sends cmd to the pcb and returns the relevant command response
    """
    ready = False
    ret = None

    try:
      answer, ready = self.query(cmd)
    except:
      raise (ValueError, "Failure while talking to PCB")

    if ready:
      # parse by question
      if cmd == 'v':
        ret = answer
      elif cmd.startswith('p'):
        ret = int(answer)
      elif cmd.startswith('c'):
        ret = answer
      else:  # parse by answer
        if answer.startswith('AIN'):
          ret = answer.split(' ')[1]
        elif answer.startswith('Board'):
          ret = int(answer.split(' ')[5])
        else:
          print(f'WARNING: Got unexpected response form PCB to "{cmd}": {answer}')
    else:
      raise (ValueError, "Comms are out of sync with the PCB")

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
  pcb_address = 'WIZnet111785'
  p = pcb(pcb_address, ignore_adapter_resistors=True)
  print(f"PD1 COUNTS = {p.getADCCounts(1)}")
