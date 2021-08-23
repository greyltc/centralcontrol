import unittest
import math
import numpy
import time

from centralcontrol.us import Us
from centralcontrol.pcb import Pcb


class UsTestCase(unittest.TestCase):
    """tests for the microstepper stage hardware"""

    motor_steps_per_rev = 200  # steps/rev
    micro_stepping = 256  # microsteps/step
    screw_pitch = 8  # mm/rev
    steps_per_mm = motor_steps_per_rev * micro_stepping / screw_pitch
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
        """test stage homing procedure. this fails if there's no PCB and no stage"""
        with Pcb(self.pcb_host, timeout=self.pcb_timeout) as p:
            me = Us(p, spm=self.steps_per_mm, homer=self.home_procedure)
            me.connect()

            home_setup = {}
            home_setup["procedure"] = self.home_procedure
            home_setup["timeout"] = 300
            home_setup["expected_lengths"] = None
            home_setup["allowed_deviation"] = None
            me.home(procedure="default", timeout=300, expected_lengths=None, allowed_deviation=None)
            for ax, ax_len in me.len_axes_mm.items():
                self.assertGreater(ax_len, 0)

    def test_goto(self):
        """tests sending the stage to places. this fails if there's no PCB and no stage"""
        test_positions = [50, 75]  # in mm

        with Pcb(self.pcb_host, timeout=self.pcb_timeout) as p:
            me = Us(p, spm=self.steps_per_mm, homer=self.home_procedure)
            me.connect()

            for ax in me.axes:
                for target_mm in test_positions:
                    me.goto({ax: target_mm})
                    pos_dict = me.get_position()
                    self.assertIsInstance(pos_dict[ax], float)
                    self.assertAlmostEqual(pos_dict[ax], target_mm)

    def test_goto_long(self):
        """tests sending the stage to a lot of places. this fails if there's no PCB and no stage"""
        n_pos_per_axis = 20

        with Pcb(self.pcb_host, timeout=self.pcb_timeout) as p:
            me = Us(p, spm=self.steps_per_mm, homer=self.home_procedure)
            me.connect()

            for ax, ax_len in me.len_axes_mm.items():
                start_pos = 1
                end_pos = math.floor(ax_len)  # pick the closest whole mm as the endpoint
                for target_mm in numpy.linspace(start_pos, end_pos, num=n_pos_per_axis):  # go to all the mm locations
                    me.goto({ax: target_mm})
                    pos_dict = me.get_position()
                    self.assertIsInstance(pos_dict[ax], float)
                    self.assertAlmostEqual(pos_dict[ax], target_mm, places=3)

    def test_dance(self):
        """do a motion dance"""
        with Pcb(self.pcb_host, timeout=self.pcb_timeout) as p:
            me = Us(p, spm=self.steps_per_mm, homer=self.home_procedure)
            me.connect()
            for ax, ax_len in me.len_axes_mm.items():
                # choose how long the dance should last
                # goto_dance_duration = float("inf")
                goto_dance_duration = 60
                print(f"\nNow doing goto dance for {goto_dance_duration} seconds...")

                dance_width_mm = 5  # width of the dance motions
                ndancepoints = 10  # number of dance centerpoints
                edge_space = 0.5  # start and end are this far from the limits

                dancemin = edge_space + dance_width_mm / 2
                dancemax = ax_len - dancemin
                dancespace = [dancemin + float(x) / (ndancepoints - 1) * (dancemax - dancemin) for x in range(ndancepoints)]
                dancepoints = []
                for p in dancespace:
                    dancepoints.append(p - dance_width_mm / 2)
                    dancepoints.append(p + dance_width_mm / 2)
                    dancepoints.append(p - dance_width_mm / 2)

                dancepoints_rev = dancepoints.copy()
                dancepoints_rev.reverse()
                del dancepoints_rev[0]
                del dancepoints_rev[-1]
                full_dancelist = dancepoints + dancepoints_rev

                t0 = time.time()
                while (time.time() - t0) < goto_dance_duration:
                    goal = full_dancelist.pop(0)
                    target_mm = goal
                    print(f"New target = {target_mm}")
                    me.goto({ax: target_mm})
                    pos_dict = me.get_position()
                    self.assertIsInstance(pos_dict[ax], float)
                    self.assertAlmostEqual(pos_dict[ax], target_mm, places=3)
                    full_dancelist.append(goal)  # allow for wrapping
                print(f"Dance complete!")
