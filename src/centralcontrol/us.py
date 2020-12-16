#!/usr/bin/env python3

import time
from collections import deque


# this boilerplate is required to allow this module to be run directly as a script
if __name__ == "__main__" and __package__ in [None, '']:
    __package__ = "centralcontrol"
    from pathlib import Path
    import sys
    # get the dir that holds __package__ on the front of the search path
    sys.path.insert(0, str(Path(__file__).parent.parent))

class us(object):
  """interface to uStepperS via i2c via ethernet connected pcb"""
  # calculate a default steps_per_mm value
  motor_steps_per_rev = 200  # steps/rev
  micro_stepping = 256  # microsteps/step
  screw_pitch = 8  # mm/rev
  steps_per_mm = motor_steps_per_rev*micro_stepping/screw_pitch
  home_procedure = "default"
  pcb = None
  len_axes_mm = [float('inf')]  # list of mm for how long the firmware thinks each axis is
  axes = [1]
  poll_delay = 0.25 # number of seconds to wait between polling events when trying to figure out if home, jog or goto are finsihed

  def __init__(self, pcb_object, spm=steps_per_mm, homer=home_procedure):
    """
    sets up the microstepper object
    needs handle to active PCB class object
    """
    self.pcb = pcb_object
    self.steps_per_mm = spm
    self.home_procedure = homer
  #def __setattr__(self, name, value):

  def __del__(self):
    pass

  # wrapper for handling firmware comms that should return ints
  def _pwrapint(self, cmd):
    answer = self.pcb.query(cmd)
    intans = 0
    try:
      intans = int(answer)
    except ValueError:
      raise(ValueError(f"Expecting integer response to {cmd}, but got {answer}"))
    return intans

  def _update_len_axes_mm(self):
    len_axes_mm = []
    for ax in self.axes:
      len_axes_mm.append(self._pwrapint(f"l{ax}")/self.steps_per_mm)
    self.len_axes_mm = len_axes_mm

  def connect(self):
    """
    opens connection to the motor controller
    and sets self.actual_lengths
    """
    self.pcb.probe_axes()
    self.axes = self.pcb.detected_axes
    self._update_len_axes_mm()
    return 0
  
  def home(self, procedure="default", timeout=float("inf"), expected_lengths=None, allowed_deviation=None):
    t0 = time.time()
    self.pcb.probe_axes()
    self.axes = self.pcb.detected_axes
    self._update_len_axes_mm()
    if procedure == "default":
      for i, ax in enumerate(self.axes):
        home_cmd = f"h{ax}"
        answer = self.pcb.query(home_cmd)
        if answer != '':
          raise(ValueError(f"Request to home axis {ax} via '{home_cmd}' failed with {answer}"))
        else:
          self._wait_for_home_or_jog(ax, timeout=timeout-time.time()-t0)
          if self.len_axes_mm[i] == 0:
            raise(ValueError(f"Homing of axis {ax} resulted in measured length of zero."))
    else:  # special home
      home_commands = procedure.split('!')
      for hcmd in home_commands:
        goal = 0
        ax = hcmd[0]
        action = hcmd[1]
        if action == 'a':
          cmd = f"j{ax}a"
        elif action == 'b':
          cmd = f"j{ax}b"
        elif action == "h":
          cmd = f"h{ax}"
        elif action == "g":
          goal = round(float(hcmd[2::])*self.steps_per_mm)
          cmd = f"g{ax}{goal}"
        else:
          raise(ValueError(f"Malformed specialized homing procedure string at {hcmd} in {procedure}"))
        answer = self.pcb.query(cmd)
        if answer != '':
          raise(ValueError(f"Error during specialized homing procedure. '{cmd}' rejected with {answer}"))
        else:
          if action in "hab":
            self._wait_for_home_or_jog(ax, timeout=timeout-time.time()-t0)
            if (action == "h"):
              ai = self.axes.index(ax)
              this_len = self.len_axes_mm[ai] 
              if (this_len== 0):
                raise(ValueError(f"Homing of axis {ax} resulted in measured length of zero."))
              elif (allowed_deviation is not None) and (expected_lengths is not None):
                el = expected_lengths[ai]
                delta = abs(this_len-el)
                if delta > allowed_deviation:
                  raise(ValueError(f"Error: Unexpected axis {ax} length. Found {this_len} [mm] but expected {el} [mm]"))
          elif action == "g":
            self._wait_for_goto(self, ax, goal, timeout=timeout-time.time()-t0, debug_prints=False)

  def _wait_for_home_or_jog(self, ax, timeout=float("inf"), debug_prints=False):
    t0 = time.time()
    ai = self.axes.index(ax)
    poll_cmd = f"l{ax}"
    answer = None
    try:
      answer = self.pcb.query(poll_cmd)
      self.len_axes_mm[ai] = int(answer)/self.steps_per_mm
    except Exception:
      print(f"Warning: got unexpected home/jog poll result: {answer}")
      self.len_axes_mm[ai] = -1/self.steps_per_mm
    dt = time.time() - t0
    while (self.len_axes_mm[ai] == -1/self.steps_per_mm) and (dt <= timeout):
      time.sleep(self.poll_delay)
      if debug_prints == True:
        print(f'{ax}-l-b-{str(self.pcb.query(f"i{ax}")).rjust(8,"0")}')  # driver status byte print for debug
      answer = None
      try:
        answer = self.pcb.query(poll_cmd)
        self.len_axes_mm[ai] = int(answer)/self.steps_per_mm
      except Exception:
        print(f"Warning: got unexpected home/jog poll result: {answer}")
        self.len_axes_mm[ai] = -1/self.steps_per_mm
      if debug_prints == True:
        print(f'{ax}-l-a-{str(self.pcb.query(f"i{ax}")).rjust(8,"0")}')   # driver status byte print for debug
      dt = time.time() - t0
    if (dt > timeout):
      raise(ValueError(f"Timeout while waiting for axis {ax} to home/jog. The duration was {dt} [s] but the limit is {timeout} [s]. The last answer was {answer}"))

  def _wait_for_goto(self, ax, goal, timeout=float("inf"), debug_prints=False):
    t0 = time.time()
    poll_cmd = f"r{ax}"
    answer = None
    answer_deque = deque([],3)
    rslt_pos = -1
    try:
      answer = self.pcb.query(poll_cmd)
      rslt_pos = int(answer)
    except Exception:
      print(f"Warning: got unexpected goto poll result: {answer}")
    answer_deque.append(answer)
    go_from = rslt_pos/self.steps_per_mm
    dt = time.time() - t0
    while (rslt_pos != goal) and (dt <= timeout):
      time.sleep(self.poll_delay)
      if debug_prints == True:
        print(f'{ax}-l-b-{str(self.pcb.query(f"i{ax}")).rjust(8,"0")}')  # driver status byte print for debug
      answer = None
      try:
        answer = self.pcb.query(poll_cmd)
        rslt_pos = int(answer)
      except Exception:
        print(f"Warning: got unexpected goto poll result: {answer}")
      answer_deque.append(answer)
      if len(answer_deque) == 3:
        if (answer_deque[0] == answer_deque[1]) and (answer_deque[1] == answer_deque[2]):
          raise(ValueError(f"Motion seems to have stopped on {ax} at {rslt_pos/self.steps_per_mm} while trying to go from ~{go_from} to {goal/self.steps_per_mm}. Recent reading results were {answer_deque}"))
      if debug_prints == True:
        print(f'{ax}-l-a-{str(self.pcb.query(f"i{ax}")).rjust(8,"0")}')   # driver status byte print for debug
      dt = time.time() - t0
    if (dt > timeout):
      raise(ValueError(f"Timeout while waiting for axis {ax} to go from {go_from} to {goal/self.steps_per_mm}. The duration was {dt} [s] but the limit is {timeout} [s]. The last answer was {answer}"))

  def goto(self, targets_mm, timeout=float("inf")):
    t0 = time.time()
    targets_step = [round(x*self.steps_per_mm) for x in targets_mm]
    for i, target_step in enumerate(targets_step):
      ax = self.axes[i]
      cmd = f"g{ax}{target_step}"
      answer = self.pcb.query(cmd)
      if answer != '':
        try:
          len_answer = self.pcb.query(f"l{ax}")
          note = f" A subsequent stage length request query returned {len_answer}. -1 indicates the stage is busy and 0 indicates it is in the unhomed state and must be homed before further movement."
        except Exception:
          note = ""
        raise(ValueError(f"Error asking axis {ax} to go to {targets_mm[i]} with response {answer}.{note}"))
    for i, target_step in enumerate(targets_step):
      ax = self.axes[i]
      self._wait_for_goto(ax, target_step, timeout=float(timeout-time.time()-t0), debug_prints=False)
    return 0

  # returns the stage's current position (a list matching the axes input)
  # axis is -1 for all available axes or a list of axes
  # returns None values for axes that could not be read
  def get_position(self):
    result_mm = []
    for ax in self.axes:
      get_cmd = f"r{ax}"
      answer = self._pwrapint(get_cmd)
      result_mm.append(answer/self.steps_per_mm)
    return result_mm

  def estop(self, axes=-1):
    """
    Emergency stop of the driver. Unpowers the motor(s)
    """
    # do it thrice because it's important
    for i in range(3):
      self.pcb.query('b')
      for ax in self.axes:
        estop_cmd = f"b{ax}"
        self.pcb.query(estop_cmd)

  def close(self):
    pass

if __name__ == "__main__":
  from .pcb import pcb
  # motion test
  pcb_address = "10.46.0.239"
  steps_per_mm = 6400
  with pcb(pcb_address) as p:
    me = us(p, spm=steps_per_mm)

    print('Connecting')
    result = me.connect()
    if result == 0:
      print('Connected!')
    else:
      raise(ValueError(f"Connection failed with {result}"))
    time.sleep(1)
    
    print('Homing')
    me.home()
    print(f"Homed!\nMeasured stage lengths = {me.len_axes_mm}")
    
    mid_mm = [x/2 for x in me.len_axes_mm]
    print(f'GOingTO the middle of the stage: {mid_mm}')
    me.goto(mid_mm)
    print("Movement done.")
    time.sleep(1)

    print('Emergency Stopping')
    me.estop()
    print('E-stopped...')
    time.sleep(10)

    print('Testing failure handling')
    try:
      me.goto(mid_mm)
    except Exception as e:
      print(f'Got an exception: {e}')

    print('Homing')
    me.home()
    print(f"Homed!\nMeasured stage lengths = {me.len_axes_mm}")

    me.close()
    print("Test complete.")
