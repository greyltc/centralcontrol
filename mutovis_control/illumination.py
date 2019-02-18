from mutovis_control.wavelabs import wavelabs

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
    if address.startswith('wavelabs'):
      self.light_engine = wavelabs(address=address)
      
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
      