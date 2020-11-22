from .wavelabs import wavelabs
#from .newport import Newport
import os

class illumination:
  """
  generic class for handling a light source
  only supports wavelabs and newport via USB (ftdi driver)
  """
  light_engine = None
  protocol = None

  def __init__(self, address='', default_recipe='am1_5_1_sun', connection_timeout = 10):
    """
    sets up communication to light source
    """

    connection_timeout = connection_timeout # s

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
      self.light_engine = wavelabs(host=host, port=port, relay=relay, connection_timeout=connection_timeout, default_recipe=default_recipe)
    #elif protocol.lower() == ('ftdi'):
    #  self.light_engine = Newport(address=address)
    self.protocol = protocol

  def connect(self):
    """
    makes connection to light source
    """
    return self.light_engine.connect()

  def on(self):
    """
    turns light on
    """
    return self.light_engine.on()

  def off(self):
    """
    turns light off
    """
    return self.light_engine.off()
  
  def get_spectrum(self):
    """
    fetches a spectrum if the light engine supports it
    """
    spec = self.light_engine.get_spectrum()
    print(f"System temperatures = {self.get_temperatures()} degC")
    return spec

  def disconnect(self):
    """
    fetches a spectrum if the light engine supports it
    """
    self.light_engine.__del__()

  def set_runtime(self, ms):
    """
    sets the recipe runtime in ms
    """
    return self.light_engine.set_runtime(ms)

  def get_runtime(self):
    """
    gets the recipe runtime in ms
    """
    return self.light_engine.get_runtime()

  def get_temperatures(self):
    """
    returns a list of light engine temperature measurements
    """
    temp = []
    if 'wavelabs' in self.protocol:
      temp.append(self.light_engine.get_vis_led_temp())
      temp.append(self.light_engine.get_ir_led_temp())
    return temp
