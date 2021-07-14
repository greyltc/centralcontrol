import collections
from .wavelabs import wavelabs
#from .newport import Newport
import os
import threading

import sys
import logging
# for logging directly to systemd journal if we can
try:
  import systemd.journal
except ImportError:
  pass


class illumination(object):
  """
  generic class for handling a light source
  only supports wavelabs and newport via USB (ftdi driver)
  """
  light_engine = None
  protocol = None
  votes_needed = 1  # for handling light state voting
  light_master = threading.Semaphore()
  connection_timeout = 10

  def __init__(self, address='', default_recipe='am1_5_1_sun', connection_timeout=10, votes_needed=1):
    """
    sets up communication to light source
    """
    # setup logging
    self.lg = logging.getLogger(__name__)
    self.lg.setLevel(logging.DEBUG)

    if not self.lg.hasHandlers():
      # set up logging to systemd's journal if it's there
      if 'systemd' in sys.modules:
        sysdl = systemd.journal.JournalHandler(SYSLOG_IDENTIFIER=self.lg.name)
        sysLogFormat = logging.Formatter(("%(levelname)s|%(message)s"))
        sysdl.setFormatter(sysLogFormat)
        self.lg.addHandler(sysdl)
      else:
        # for logging to stdout & stderr
        ch = logging.StreamHandler()
        logFormat = logging.Formatter(("%(asctime)s|%(name)s|%(levelname)s|%(message)s"))
        ch.setFormatter(logFormat)
        self.lg.addHandler(ch)

    self.connection_timeout = connection_timeout  # s

    self.votes_needed = votes_needed
    if self.votes_needed > 1:
      self.on_votes = collections.deque([], maxlen=self.votes_needed)

    addr_split = address.split(sep='://', maxsplit=1)
    protocol = addr_split[0]
    if protocol.lower() == 'env':
      env_var = addr_split[1]
      if env_var in os.environ:
        address = os.environ.get(env_var)
      else:
        raise ValueError("Environment Variable {:} could not be found".format(env_var))
      addr_split = address.split(sep='://', maxsplit=1)
      protocol = addr_split[0]

    if protocol.lower().startswith('wavelabs'):
      location = addr_split[1]
      ls = location.split(':')
      host = ls[0]
      if len(ls) == 1:
        port = None
      else:
        port = int(ls[1])
      if 'relay' in protocol.lower():
        relay = True
      else:
        relay = False
      self.light_engine = wavelabs(host=host, port=port, relay=relay, connection_timeout=self.connection_timeout, default_recipe=default_recipe)
    #elif protocol.lower() == ('ftdi'):
    #  self.light_engine = Newport(address=address)
    self.protocol = protocol

    self.lg.debug(f"{__name__} initialized.")

  def connect(self):
    """
    makes connection to light source
    """
    self.lg.debug("ill connect() called")
    ret = self.light_engine.connect()
    self.lg.debug("ill connect() compelte")
    return ret

  def on(self, assume_master=False):
    # thread safe light control with unanimous state voting
    self.lg.debug("ill on() called")
    do_light_action = True
    if (self.votes_needed > 1) and (assume_master == False):
      self.on_votes.append(True)
      if self.light_master.acquire(blocking=False):
        # we're the light master!
        while self.on_votes.count(True) < self.votes_needed:
          pass  # wait for everyone to agree
        self.lg.debug("Light voting complete!")
      else:
        self.lg.debug("Light vote submitted")
        do_light_action = False

    if do_light_action == True:
      ret = self.light_engine.on()
      if (self.votes_needed > 1) and (assume_master == False):
        self.light_master.release()
    else:
      ret = 0

    self.lg.debug("ill on() complete")
    return ret

  def off(self, assume_master=False):
    # thread safe light control with unanimous state voting
    self.lg.debug("ill off() called")
    do_light_action = True
    if (self.votes_needed > 1) and (assume_master == False):
      self.on_votes.append(False)
      if self.light_master.acquire(blocking=False):
        # we're the light master!
        while self.on_votes.count(False) < self.votes_needed:
          pass  # wait for everyone to agree
        self.lg.debug("Light voting complete!")
      else:
        self.lg.debug("Light vote submitted")
        do_light_action = False

    if do_light_action == True:
      ret = self.light_engine.off()
      if (self.votes_needed > 1) and (assume_master == False):
        self.light_master.release()
    else:
      ret = 0

    self.lg.debug("ill off() complete")
    return ret

  def get_spectrum(self):
    """
    fetches a spectrum if the light engine supports it
    """
    self.lg.debug("ill get_spectrum() called")
    spec = self.light_engine.get_spectrum()
    self.lg.debug("ill get_spectrum() complete")
    self.get_temperatures()  # just to trigger the logging
    return spec

  def disconnect(self):
    """
    clean up connection to light
    """
    self.lg.debug("ill disconnect() called")
    self.__del__()
    self.lg.debug("ill disconnect() complete")

  def set_runtime(self, ms):
    """
    sets the recipe runtime in ms
    """
    self.lg.debug(f"ill set_runtime({ms=}) called")
    ret = self.light_engine.set_runtime(ms)
    self.lg.debug("ill set_runtime() complete")
    return ret

  def get_runtime(self):
    """
    gets the recipe runtime in ms
    """
    self.lg.debug("ill get_runtime() called")
    runtime = self.light_engine.get_runtime()
    self.lg.debug(f"ill get_runtime() complete with {runtime=}")
    return runtime

  def set_intensity(self, percent):
    """
    sets the recipe runtime in ms
    """
    self.lg.debug(f"ill set_intensity({percent=}) called")
    ret = self.light_engine.set_intensity(percent)
    self.lg.debug("ill set_intensity() complete")
    return ret

  def get_intensity(self):
    """
    gets the recipe runtime in ms
    """
    self.lg.debug("ill get_intensity() called")
    intensity = self.light_engine.get_intensity()
    self.lg.debug(f"ill get_intensity() complete with {intensity=}")
    return intensity

  def get_temperatures(self):
    """
    returns a list of light engine temperature measurements
    """
    self.lg.debug("ill get_temperatures() called")
    temp = []
    if 'wavelabs' in self.protocol:
      temp.append(self.light_engine.get_vis_led_temp())
      temp.append(self.light_engine.get_ir_led_temp())
    self.lg.debug(f"ill get_temperatures() complete with {temp=}")
    return temp

  def __del__(self):
    self.lg.debug("ill __del__() called")
    if hasattr(self, "light_engine"):
      del (self.light_engine)
    self.light_engine = None
    self.lg.debug("ill __del__() complete")
