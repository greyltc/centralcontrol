import unittest
import math
import numpy
import time

from centralcontrol.xdac import Xdac
import zmq


class XdacTestCase(unittest.TestCase):
    """tests for the XDAC from nicslab"""

    xdac_host = "10.42.0.166"
    ctx = None

    def setUp(self):
        self.ctx = zmq.Context()

    def test_init(self):
        """class init"""
        x = Xdac(self.ctx, ip=self.xdac_host)
        self.assertIsInstance(x, Xdac)

    def test_read_v(self):
        """prints all channel vonltage readings for a few seconds"""
        read_duration = 3  # s

        x = Xdac(self.ctx, ip=self.xdac_host)

        t0 = time.time()
        i = 0
        while (time.time() - t0) < read_duration:
            i = i + 1
            val_string = str([f"{v:07.2f}" for v in x.readAllChannelVoltage()])
            print(f"Voltage = {val_string} V {i}")

    def test_read_i(self):
        """prints all channel current readings for a few seconds"""
        read_duration = 3  # s

        x = Xdac(self.ctx, ip=self.xdac_host)

        t0 = time.time()
        i = 0
        while (time.time() - t0) < read_duration:
            i = i + 1
            val_string = str([f"{v:07.2f}" for v in x.readAllChannelCurrent()])
            print(f"Current = {val_string} mA {i}")

    def test_current_offset(self):
        """tests current zero offset finding (channel terminals should be disconnected for this, but does not mod calibration file)"""
        x = Xdac(self.ctx, ip=self.xdac_host)
        offsets = x.find_current_zero_offsets()
        print(f"Current zero-offsets = {offsets} mA")

    def test_set_v(self):
        """test channel voltage setting"""
        v_setpoints = [-10, 3, -4, 5, 9, -1, 7, -4]

        x = Xdac(self.ctx, ip=self.xdac_host)
        x.setVoltageAllChannels(v_setpoints)

    def test_set_off(self):
        """test channel turning off"""

        x = Xdac(self.ctx, ip=self.xdac_host)
        for ch in range(x.n_chans):
            x.setOff(ch + 1)
