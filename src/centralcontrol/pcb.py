#!/usr/bin/env python3

from telnetlib import Telnet
import socket
import os

class pcb(object):
  """
  Interface for talking to the control PCB
  """
  write_terminator = '\r\n'
  #read_terminator = b'\r\n'
  prompt_string = '>>> '
  prompt = prompt_string.encode()
  #prompt = read_terminator + prompt_string.encode()
  comms_timeout = socket._GLOBAL_DEFAULT_TIMEOUT
  telnet_host = "localhost"
  telnet_port = 23
  firmware_version = 'unknown'
  detected_muxes = []
  detected_axes = []

  class MyTelnet(Telnet):
    def read_response(self, timeout=None):
      found_prompt = False
      resp = self.read_until(pcb.prompt, timeout=timeout)
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

  def __init__(self, address=None, timeout=comms_timeout):
    self.comms_timeout = timeout # pcb has this many seconds to respond

    if address is not None:
      addr_split = address.split(':')
      if len(addr_split) == 1:
        self.telnet_host = addr_split[0]
      else:
        h, p = address.split(':')
        self.telnet_host = h
        self.telnet_port = int(p)

  def __enter__(self):
    self.tn = self.MyTelnet(self.telnet_host, self.telnet_port, timeout=self.comms_timeout)
    self.sf = self.tn.sock.makefile("rwb", buffering=0)

    if os.name != 'nt':
      pcb.set_keepalive_linux(self.tn.sock)  # let's try to keep our connection alive!

    welcome_message, win = self.tn.read_response()

    if not win:
      raise ValueError('Firmware did not present command prompt on connection')

    self.firmware_version = self.query('v')
    self.probe_muxes()
    self.probe_axes()
    print(f"v={self.firmware_version}|m={self.detected_muxes}|s={self.detected_axes}")
    return(self)

  # figures out what muxes are connected
  def probe_muxes(self):
    mux_int = int(self.query('c'))
    mux_bin_str = f"{mux_int:b}"
    mux_bin_str_rev = mux_bin_str[::-1]
    self.detected_muxes = []
    start_char = 'A'
    for i, b in enumerate(mux_bin_str_rev):
      if b == '1':
        self.detected_muxes.append(chr(ord(start_char)+i))

# figures out what axes are connected
  def probe_axes(self):
    axes_int = int(self.query('e'))
    axes_bin_str = f"{axes_int:b}"
    axes_bin_str_rev = axes_bin_str[::-1]
    self.detected_axes = []
    start_char = '1'
    for i, b in enumerate(axes_bin_str_rev):
      if b == '1':
        self.detected_axes.append(chr(ord(start_char)+i))

  def __exit__(self, type, value, traceback):
    try:
      self.write(self, "exit")
    except Exception:
      pass
    try:
      self.sf.close()
    except Exception:
      pass
    try:
      self.tn.close()
    except Exception:
      pass

  def pix_picker(self, substrate, pixel, suppressWarning=False):
    win = False
    ready = False
    retries = 5
    try_num = 0
    while try_num < retries:
      try:
        cmd = "s" + substrate + str(pixel)
        answer, ready = self._query(cmd)
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

  # query with no ack check
  def _query(self, query):
    self.write(query)
    return self.tn.read_response()

  # query with better error handling and with ack check
  def query(self, query):
    answer = None
    ack = False
    try:
      answer, ack = self._query(query)
    except Exception:
      raise(ValueError(f"Firmware comms failure while trying to send '{query}'"))
    if ack == False:
      raise(ValueError(f"Firmware did not acknowledge '{query}'"))
    return answer

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
        answer, ready = self._query(cmd)
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
          ret = answer
        else:
          ret = answer
      elif cmd.startswith('l'):
        if answer.startswith('ERROR'):
          ret = answer
        else:
          ret = int(answer)
      elif cmd.startswith('r'):
        if answer.startswith('ERROR'):
          ret = answer
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
  with pcb(pcb_address) as p:
    print(f"Mux Check result = {p.get('c')}")
    print(f"Stage Check result = {p.get('e')}")
