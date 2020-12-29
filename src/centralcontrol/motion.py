from .afms import afms
from .us import us
import json
from urllib.parse import urlparse, parse_qs

# this boilerplate is required to allow this module to be run directly as a script
if __name__ == "__main__" and __package__ is None:
    __package__ = "centralcontrol"
    from pathlib import Path
    import sys
    # get the dir that holds __package__ on the front of the search path
    sys.path.insert(0, str(Path(__file__).parent.parent))


class motion:
  """
  generic class for handling substrate movement
  """
  motion_engine = None
  home_procedure = "default"
  home_timeout = 130  # seconds
  motion_timeout_fraction = 1/2  # fraction of home_timeout for movement timeouts
  expected_lengths = [float("inf")]  # list of mm
  actual_lengths = [float("inf")]  # list of mm
  keepout_zones = [[-2,-2]]  # list of lists of mm
  axes = [1]  # list of connected axis indicies
  allowed_length_deviation = 5 # measured length can deviate from expected length by up to this, in mm
  location = "controller"

  motor_steps_per_rev = 200  # steps/rev
  micro_stepping = 256  # microsteps/step
  screw_pitch = 8  # mm/rev
  steps_per_mm = motor_steps_per_rev*micro_stepping/screw_pitch

  address = "us://controller"

  def __init__(self, address=address, pcb_object=None):
    """
    sets up communication to motion controller
    """
    parsed = None
    qparsed = None
    try:
      parsed = urlparse(address)
      qparsed = parse_qs(parsed.query)
    except Exception:
      raise(ValueError("Incorrect motion controller address format: {address}"))
    self.location = parsed.netloc + parsed.path
    empty_koz = [-2, -2]  # a keepout zone that will never activate
    if "el" in qparsed:
      splitted = qparsed['el'][0].split(',')
      self.expected_lengths = [float(y) for y in splitted]
      self.keepout_zones = []
      for i, l in enumerate(self.expected_lengths):  # ensure default koz works
        self.keepout_zones.append(empty_koz)
    if "spm" in qparsed:
      self.steps_per_mm = int(qparsed['spm'][0])
    if "kz" in qparsed:
      self.keepout_zones = json.loads(qparsed['kz'][0])
      for i, z in enumerate(self.keepout_zones):
        if z == []:
          self.keepout_zones[i] = empty_koz
    if "hto" in qparsed:
      self.home_timeout = float(qparsed['hto'][0])
    if "homer" in qparsed:
      self.home_procedure = qparsed['homer'][0]
    if "lf" in qparsed:
      self.allowed_length_deviation = float(qparsed['lf'][0])

    if parsed.scheme =='afms':
      if pcb_object is not None:
        pass  #TODO: throw warning here if we detect a virtual PCB object because afms does not support this.
      afms_setup = {}
      afms_setup["location"] = self.location
      afms_setup["spm"] = self.steps_per_mm
      afms_setup["homer"] = self.home_procedure
      self.motion_engine = afms(**afms_setup)
    elif parsed.scheme == 'us':
      if self.location != "controller":
        raise(ValueError(f"Stage connection location unknown: {self.location}"))
      else:
        if pcb_object is None:
          raise(ValueError(f"us:// protocol requires a pcb_object"))
        else:
          us_setup = {}
          us_setup["pcb_object"] = pcb_object
          us_setup["spm"] = self.steps_per_mm
          if hasattr(pcb_object, 'is_virtual'):
            if pcb_object.is_virtual == True:
              pcb_object.prepare_virt_motion(spm=self.steps_per_mm, el=self.expected_lengths)
          self.motion_engine = us(**us_setup)
    else:
      raise(ValueError(f"Unexpected motion controller protocol {self.scheme} in {address}"))

  def connect(self):
    """
    makes connection to motion controller and does a light check that the given axes config is correct
    """
    result = self.motion_engine.connect()
    if result == 0:
      self.actual_lengths = self.motion_engine.len_axes_mm
      self.axes = self.motion_engine.axes

      naxes = len(self.axes)
      nlengths = len(self.actual_lengths)
      nexpect = len(self.expected_lengths)
      nzones = len(self.keepout_zones)

      if naxes != nlengths:
        raise(ValueError(f"Error: axis count mismatch. Measured {nlengths} lengths, but the hardware reports {naxes} axes"))
      if naxes != nexpect:
        raise(ValueError(f"Error: axis count mismatch. Found {nexpect} expected lengths, but the hardware reports {naxes} axes"))
      if naxes != nzones:
        raise(ValueError(f"Error: axis count mismatch. Found {nexpect} keepout zone lists, but the hardware reports {naxes} axes"))
      
      for i, a in enumerate(self.axes):
        if self.actual_lengths[i] <= 0:
          print(f"Warning: axis {a} is not ready for motion. Homing recommended.")

    return result

#  def move(self, mm):
#    """
#    moves mm mm direction, blocking, returns 0 on successful movement
#    """
#    return self.motion_engine.move(mm)

  def goto(self, pos, timeout=None):
    """
    goes to an absolute mm position, blocking, reuturns 0 on success
    """
    if timeout == None:
      timeout = self.home_timeout*self.motion_timeout_fraction
    if not hasattr(pos, "__len__"):
      pos = [pos]
    naxes = len(self.axes)
    npos = len(pos)
    if naxes != npos:
      raise(ValueError(f"Error: axis count mismatch. Found {npos} commanded positions, but the hardware reports {naxes} axes"))
    for i, a in enumerate(self.axes):
      el = self.expected_lengths[i]
      al = self.actual_lengths[i]
      ko_lower = self.keepout_zones[i][0]
      ko_upper = self.keepout_zones[i][1]
      lower_lim = 0 + self.motion_engine.end_buffers
      upper_lim = al - self.motion_engine.end_buffers
      goal = pos[i]
      if el < float("inf"):  # length check is enabled
        delta = el - al
        if abs(delta) > self.allowed_length_deviation:
          raise(ValueError(f"Error: Unexpected axis {a} length. Found {al} [mm] but expected {el} [mm]"))
      if (goal >= ko_lower) and (goal <= ko_upper):
        raise(ValueError(f"Error: Axis {a} requested position, {goal} [mm], falls within keepout zone: [{ko_lower}, {ko_upper}] [mm]"))
      if goal < lower_lim:
        raise(ValueError(f"Error: Attempt to move axis {a} outside of limits. Attempt: {goal} [mm], but Minimum: {lower_lim} [mm]"))
      if goal > upper_lim:
        raise(ValueError(f"Error: Attempt to move axis {a} outside of limits. Attempt: {goal} [mm], but Maximum: {upper_lim} [mm]"))
    goto_result = self.motion_engine.goto(pos, timeout=timeout)
    return goto_result

  def home(self, timeout=None):
    """
    homes to a limit switch, blocking, reuturns 0 on success
    """
    if timeout is None:
      timeout = self.home_timeout
    home_setup = {}
    home_setup["procedure"] = self.home_procedure
    home_setup["timeout"] = timeout
    home_setup["expected_lengths"] = self.expected_lengths
    home_setup["allowed_deviation"] = self.allowed_length_deviation
    home_result = self.motion_engine.home(**home_setup)
    self.actual_lengths = self.motion_engine.len_axes_mm

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

