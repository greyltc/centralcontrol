#!/usr/bin/env python3
import serial

class afms:
  """interface to an arduino with an adafruit motor shield connected via a USB virtual serial port, custom sketch"""
  protocol = 'afms'
  steps_per_mm = 10
  com_port = ''
  current_position = 50 / steps_per_mm  #  in mm
  
  substrate_centers = [300, 260, 220, 180, 140, 100, 60, 20]  # mm from home to the centers of A, B, C, D, E, F, G, H substrates
  photodiode_location = 315  # mm
  
  # software reject movements that would put us outside these limits
  minimum_position = 50 / steps_per_mm  #  in mm
  maximum_position = 330  # mm

  def __init__(self, address="afms:///dev/ttyACM0"):
    """
    sets up the afms object
    address is a string of the format:
    afms://serial_port_location
    for example "afms:///dev/ttyACM0"
    """
    self.protocol, self.com_port = address.split('://')
    
  def __del__(self):
    try:
      self.close()
    except:
      pass

  def connect(self):
    """
    opens connection to the motor controller via its com port and homes
    returns 0 on success
    """
    ret = -1
    if self.protocol == 'afms':
      self.connection = serial.Serial(self.com_port)
      # might need to purge read buffer here
      self.connection.timeout = 30  # moving should never take longer than this many seconds
      ret = self.home()
    else:
      print("WRNING: Got unexpected afms motion controller comms protocol: {:}".format(self.protocol))
      ret = -3
    return ret


  def home(self):
    """
    homes to the negative limit switch
    """
    ret = self.move(-10000000)  # home (aka try to move 10 km in reverse, hitting that limit switch)
    if ret == 0:
      self.current_position = 50 / self.steps_per_mm  # set position to be limit backoff
    else:
      print('WARNING: homing failure: {:}'.format(ret))
    return ret
    
      
  def move(self, mm):
    """
    moves mm mm, blocks until movement complete, mm can be positive or negative to indicate movement direction
    rejects movements outside limits
    returns 0 upon sucessful move
    """
    sc = self.connection
    
    steps = round(mm*steps_per_mm)
    new_position = self.current_position + steps * self.steps_per_mm
    if (new_position < self.minimum_position) or (new_position > self.maximum_position):
      print("WARNING: Movement request rejected because requested position {:} is outside software limits".format(new_position))
      return -1  # failed movement
    
    if steps > 0:
      direction = 'forward'
    elif steps < 0:
      direction = 'backward'
    else:
      direction = None
    
    if direction != None:
      # send movement command
      sc.write('step,{:},{:}'.format(abs(steps), direction).encode())
      # read five bytes
      idle_message = sc.read(1) + sc.read(1) + sc.read(1) + sc.read(1) + sc.read(1)
      idle_message = idle_message.decode()
      if idle_message.startswith('idle'):
        self.current_position = new_position  # store new position on successful movement
      else:
        print("WARNING: Expected idle message after movement, insted: {:}".format(idle_message))
        return -2  # failed movement
        
    return 0  # sucessful movement

  def goto(self, new_position):
    """
    goes to an absolute mm position, blocking, returns 0 on success
    """
    return self.move(new_position-self.current_position)
    
  def close(self):
    self.connection.close()

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