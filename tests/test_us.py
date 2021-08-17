import unittest
import time

from centralcontrol.us import Us
from centralcontrol.pcb import Pcb


class UsTestCase(unittest.TestCase):
    """tests for the microstepper stage hardware"""

    motor_steps_per_rev = 200  # steps/rev
    micro_stepping = 256  # microsteps/step
    screw_pitch = 8  # mm/rev
    steps_per_mm = motor_steps_per_rev * micro_stepping * screw_pitch
    home_procedure = "default"

    pcb_host = "WIZnet111785"
    pcb_timeout = 1

    def test_init(self):
        """class init"""
        pcb_object = "dummy"
        me = Us(pcb_object, spm=self.steps_per_mm, homer=self.home_procedure)
        self.assertIsInstance(me, Us)

    def test_connect(self):
        """conntction to hardware. this fails if there's no PCB and no stage"""
        with Pcb(self.pcb_host, timeout=self.pcb_timeout) as p:
            me = Us(p, spm=self.steps_per_mm, homer=self.home_procedure)
            me.connect()
            self.assertGreater(len(me.stage_firmwares), 0)
            for fw in me.stage_firmwares:
                self.assertIsInstance(fw, str)

    def test_home(self):
        """conntction to hardware. this fails if there's no PCB and no stage"""
        with Pcb(self.pcb_host, timeout=self.pcb_timeout) as p:
            me = Us(p, spm=self.steps_per_mm, homer=self.home_procedure)
            me.connect()

            home_setup = {}
            home_setup["procedure"] = self.home_procedure
            home_setup["timeout"] = 300
            home_setup["expected_lengths"] = None
            home_setup["allowed_deviation"] = None
            me.home(procedure="default", timeout=300, expected_lengths=None, allowed_deviation=None)
