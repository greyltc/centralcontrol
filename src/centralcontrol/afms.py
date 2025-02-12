#!/usr/bin/env python3
import serial


class AFMS(object):
    """interface to an arduino with an adafruit motor shield connected via a USB virtual serial port, custom sketch"""

    steps_per_mm = 10
    com_port = "/dev/ttyACM0"
    current_position = 50 / steps_per_mm  #  in mm
    home_procedure = "default"

    len_axes_mm: dict # list of mm for how long the firmware thinks each axis is
    axes: list[str]  # list of connected axis indicies

    end_buffers = 1  # disallow movement to closer than this many mm from an end (prevents home issues)

    def __init__(self, location=com_port, spm=steps_per_mm, homer=home_procedure):
        """sets up the afms object"""
        self.com_port = location
        self.steps_per_mm = spm
        self.home_procedure = homer
        self.axes = ["1"]
        self.len_axes_mm = {"0": float("inf")} 

    def __del__(self):
        try:
            self.close()
        except:
            pass

    def connect(self) -> int:
        """opens connection to the motor controller via its com port and homes"""
        self.connection = serial.Serial(self.com_port)
        # might need to purge read buffer here
        if self.connection.is_open == True:
            ret = 0
        else:
            ret = -1
            raise (ValueError(f"Unable to open port {self.com_port}"))
        self.axes = ["1"]
        return ret

    def home(self, timeout=300.0, procedure=home_procedure, expected_lengths=None, allowed_deviation=None):
        """homes to the negative limit switch"""
        ret = self.move(-10000000)  # home (aka try to move 10 km in reverse, hitting that limit switch)
        self.current_position = 0
        if ret == 0:
            ret = self.move(50 / self.steps_per_mm)  # move away from the edge by 50 steps
        else:
            print(f"WARNING: homing failure: {ret}")
        self.len_axes_mm = {"0": float("inf")}  # length measurement unsupported here now
        return ret

    def move(self, mm, timeout=300.0):
        """
        moves mm mm, blocks until movement complete, mm can be positive or negative to indicate movement direction
        rejects movements outside limits
        returns 0 upon sucessful move
        """
        sc = self.connection

        steps = round(mm * self.steps_per_mm)

        if steps > 0:
            direction = "forward"
        elif steps < 0:
            direction = "backward"
        else:
            direction = None

        if direction != None:
            # send movement command
            sc.write(f"step,{abs(steps)},{direction}".encode())
            # read five bytes
            idle_message = sc.read(1) + sc.read(1) + sc.read(1) + sc.read(1) + sc.read(1)
            idle_message = idle_message.decode()
            if idle_message.startswith("idle"):
                self.current_position = self.current_position + steps * self.steps_per_mm  # store new position on successful movement
            else:
                print(f"WARNING: Expected idle message after movement, insted: {idle_message}")
                return -2  # failed movement

        return 0  # sucessful movement

    def goto(self, new_position, timeout=300.0, debug_prints=False):
        """
        goes to an absolute mm position, blocking, returns 0 on success
        """
        return self.move(new_position - self.current_position, timeout=timeout)

    def close(self):
        self.connection.close()

    def estop(self):
        pass  # TODO: probably do this with set-speed

    def get_position(self):
        return [self.current_position]


if __name__ == "__main__":
    import time

    # motion test
    com_port = "/dev/ttyACM0"
    this = AFMS(com_port)

    print("Connecting and homing...")
    if this.connect() == 0:
        print("Homing done!")
    time.sleep(1)

    print("Moving 4cm forwad via move")
    if this.move(40) == 0:
        print("Movement done.")
    time.sleep(1)

    print("Moving 2cm backward via goto")
    if this.goto(this.current_position - 20) == 0:
        print("Movement done.")
    time.sleep(1)

    print("Homing...")
    if this.home() == 0:
        print("Homing done!")

    this.close()
    print("Test complete.")
