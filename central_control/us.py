#!/usr/bin/env python3

from collections import deque
import time

class us:
  """interface to uStepperS via i2c via ethernet connected pcb"""
  
  substrate_centers = [300, 260, 220, 180, 140, 100, 60, 20]  # mm from home to the centers of A, B, C, D, E, F, G, H substrates
  photodiode_location = 315  # mm
  
  # software reject movements that would put us outside these limits
  # TODO: make multi axis
  minimum_position = [5]
  maximum_position = [120]

  def __init__(self, pcb_object, nAxes=1, expected_lengths=[809219], steps_per_mm=200*256/2.54):
    """
    sets up the microstepper object
    needs handle to active PCB class object
    """
    self.pcb = pcb_object
    self.steps_per_mm = steps_per_mm
    self.nAxes = nAxes

    for i in range(nAxes):
      self.current_position[i] = None
      self.expected_length[i] = expected_lengths[i]  # in steps
    
    
  def __del__(self):
      pass

  def connect(self):
    """
    opens connection to the motor controller
    """
    len0 = pcb.get('l0')  # get the length of the 0 axis to check comms
    return 0


  def home(self):
    """
    homes to the negative limit switch
    """
    ret = 0
    for i in range(len(self.expected_length)):
      self.pcb.query(f"h{i}")

    # wait for the homings to complete
    for i in range(len(self.expected_length)):
      stage_length = self.pcb.get(f"l{i}")
      while (stage_length == -1):
        time.sleep(0.5)
        stage_length = self.pcb.get(f"l{i}")
      if (stage_length < self.expected_length[i]*0.95) or (stage_length > self.expected_length[i]*1.05):
        ret = -1
        raise ValueError("Move error")  #TODO: log movement error

      self.current_position[i] = self.pcb.get(f'r{i}')/self.steps_per_mm

    return ret


  def move(self, mm):
    """
    moves mm mm, blocks until movement complete, mm can be positive or negative to indicate movement direction
    rejects movements outside limits
    returns 0 upon sucessful move
    """
    ret = -1

    if not hasattr(mm, "__len__"):
      mm = [mm]

    if len(mm) != self.nAxes:
      raise ValueError("Move error")  #TODO: log movement error
    else:
      where = [0]*self.nAxes  # final locations
      for i in len(mm):
        here = self.pcb.get(f'r{i}')
        where[i] = round(here + mm[i]*self.steps_per_mm)
      ret = self.goto(where)
    return ret


  def goto(self, new_pos):
    """
    goes to an absolute mm position, blocking, returns 0 on success
    """
    ret = 0

    if not hasattr(new_pos, "__len__"):
      new_pos = [new_pos]

    if len(new_pos) != self.nAxes:
      raise ValueError("Move error")  #TODO: log movement error
      ret = -1
    else:
      for i in len(new_pos):
        new = round(new_pos[i]*self.steps_per_mm) 
        if (new > maximum_position[i]) or (new < maximum_position[i]):  # out of bounds
          raise ValueError("Move error")  #TODO: log movement error
          ret = -1
          break
        else:
          resp = self.pcb.query(f'g{i}{new}')
          if resp != "":
            raise ValueError("Move error")  #TODO: log movement error
            ret = -1
            break

      # now let's wait for all the motion to be done
      for i in len(new_pos):
        q = deque([-1, -2], 2)
        while q[0] != q[1]:  # while the last two readings are not equal
          q.append(self.pcb.get(f'r{i}'))
          time.sleep(0.5)
        self.current_position[i] = q[0]/self.steps_per_mm

    return ret


  def close(self):
    pass

if __name__ == "__main__":
  # motion test
  com_port = '/dev/ttyACM0'
  address = 'afms://' + com_port
  me = afms(address)

  print('Connecting and homing...')
  if me.connect() == 0:
    print('Homing done!')
  time.sleep(1)

  print('Moving 4cm forwad via move')
  if (me.move(40) == 0):
    print("Movement done.")
  time.sleep(1)

  print('Moving 2cm backward via goto')
  if (me.goto(current_position-20) == 0):
    print("Movement done.")
  time.sleep(1)

  print('Homing...')
  if me.home() == 0:
    print('Homing done!')

  me.close()
  print("Test complete.")
