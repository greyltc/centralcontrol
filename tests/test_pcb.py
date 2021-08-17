import unittest
import time

from centralcontrol.pcb import Pcb


class PcbTestCase(unittest.TestCase):
    """tests for the pcb and controller"""

    pcb_host = "WIZnet111785"
    pcb_timeout = 1
    expected_muxes = ["A", "B", "C", "D", "E", "F", "G", "H"]

    def test_init(self):
        """class init"""
        p = Pcb(addess=self.pcb_host, timeout=self.pcb_timeout, expected_muxes=self.expected_muxes)
        self.assertIsInstance(p, Pcb)

    def test_connect(self):
        """conntction to hardware. this fails if there's no PCB and no mux"""
        with Pcb(self.pcb_host, timeout=self.pcb_timeout) as p:
            self.assertIsInstance(p.welcome_message, str)
            print("Got welcome message:")
            print(p.welcome_message)
            self.assertIsInstance(p.firmware_version, str)
            self.assertTrue("+" in p.firmware_version)
