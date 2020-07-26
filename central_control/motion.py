from central_control.afms import afms
from central_control.us import us

class motion:
  """
  generic class for handling substrate movement
  """
  motion_engine = None

  # these should be overwritten by a motion controller implementation
  #substrate_centers = [160, 140, 120, 100, 80, 60, 40, 20]  # mm from home to the centers of A, B, C, D, E, F, G, H substrates
  #photodiode_location = 180  # mm  

  def __init__(self, address='', pcb_object = None):
    """
    sets up communication to motion controller
    """
    if address.startswith('afms://'):  # adafruit motor shield
      self.motion_engine = afms(address=address)
      self.substrate_centers = self.motion_engine.substrate_centers
      self.photodiode_location = self.motion_engine.photodiode_location
    elif address.startswith('us://'):  # uStepperS via i2c via ethernet connected pcb
      content = address.lstrip('us://')
      pieces = content.split('/', maxsplit=2)
      expected_lengths_in_mm = pieces[0]
      steps_per_mm = float(pieces[1])
      if len(pieces) == 3:
        extra = pieces[2]
      else:
        extra = ''

      expected_lengths_in_mm = expected_lengths_in_mm.split(',')
      expected_lengths_in_mm = [float(x) for x in expected_lengths_in_mm]
      steps_per_mm = round(steps_per_mm)

      self.motion_engine = us(pcb_object, expected_lengths=expected_lengths_in_mm, steps_per_mm=steps_per_mm, extra=extra)
      #self.substrate_centers = self.motion_engine.substrate_centers
      #self.photodiode_location = self.motion_engine.photodiode_location



  def connect(self):
    """
    makes connection to motion controller, blocking
    """
    return self.motion_engine.connect()

  def move(self, mm):
    """
    moves mm mm direction, blocking, returns 0 on successful movement
    """
    return self.motion_engine.move(mm)

  def goto(self, pos):
    """
    goes to an absolute mm position, blocking, reuturns 0 on success
    """
    return self.motion_engine.goto(pos)

  def home(self):
    """
    homes to a limit switch, blocking, reuturns 0 on success
    """
    return self.motion_engine.home()

  def estop(self):
    """
    emergency stop of the driver
    """
    return self.motion_engine.estop()

  def get_position(self):
    """
    returns the current stage location in mm
    """
    return self.motion_engine.get_position()