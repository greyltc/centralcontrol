from mutovis_control.wavelabs import wavelabs
from mutovis_control.newport import Newport
import os

class illumination:
  """
  generic class for handling a light source
  only supports wavelabs for now
  """
  light_engine = None

  def __init__(self, address=''):
    """
    sets up communication to light source
    """
    addr_split = address.split(sep='://', maxsplit=1)
    protocol = addr_split[0]
    if protocol.lower() == 'env':
      env_var = addr_split[1]
      if env_var in os.environ:
        address = environ.get(env_var)
      else:
        raise ValueError("Environment Variable {:} could not be found".format(env_var))
      addr_split = address.split(sep='://', maxsplit=1)
      protocol = addr_split[0]

    if protocol.lower() == 'wavelabs':
      self.light_engine = wavelabs(address=address)
    elif protocol.lower() == ('ftdi'):
      self.light_engine = Newport(address=address)
      
  def connect(self):
    """
    makes connection to light source
    """
    self.light_engine.connect()
    
  def on(self):
    """
    turns light on
    """
    self.light_engine.on()
    
  def off(self):
    """
    turns light off
    """
    self.light_engine.off()
      