from mutovis_control.afms import afms

class motion:
  """
  generic class for handling substrate movement under the light source
  """
  motion_engine = None

  def __init__(self, address=''):
    """
    sets up communication to motion controller
    """
    if address.startswith('afms'):
      self.motion_engine = afms(address=address)
      
  def connect(self):
    """
    makes connection to motion controller
    """
    self.motion_engine.connect()
    
  def move(self, steps, direction):
    """
    moves steps steps in direction direction, blocking
    """
    self.motion_engine.move(steps, direction)
    
  #def goto(self, step_value):
    #"""
    #goes to a step position, blocking
    #"""
    #self.motion_engine.goto(step_value)
    
  #def home(self, direction):
    #"""
    #homes to a limit switch, blocking
    #"""
    #self.motion_engine.home(direction)
      