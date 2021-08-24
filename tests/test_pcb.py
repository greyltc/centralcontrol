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
        with Pcb(self.pcb_host, timeout=self.pcb_timeout, expected_muxes=self.expected_muxes) as p:
            self.assertIsInstance(p.welcome_message, str)
            print("Got welcome message:")
            print(p.welcome_message)
            self.assertIsInstance(p.firmware_version, str)
            self.assertTrue("+" in p.firmware_version)

    def test_mux_once(self):
        """manipulate the mux"""
        em = self.expected_muxes
        # em = []
        with Pcb(self.pcb_host, timeout=self.pcb_timeout, expected_muxes=em) as p:
            ret, found_prompt = p.query_nocheck("s")  # deselect
            self.assertTrue(found_prompt)
            self.assertIsInstance(ret, str)
            self.assertEqual(len(ret), 0)

            ret, found_prompt = p.query_nocheck("sA515")  # make a typical selection (slot A, device #2)
            self.assertTrue(found_prompt)
            self.assertIsInstance(ret, str)
            self.assertEqual(len(ret), 0)

            ret, found_prompt = p.query_nocheck("s")  # deselect
            self.assertTrue(found_prompt)
            self.assertIsInstance(ret, str)
            self.assertEqual(len(ret), 0)

    def test_mux_alot(self):
        """manipulate the mux 'alot'"""
        em = self.expected_muxes
        print(f"\nFound muxes = {em}")
        n_repeats = 10
        n_bits = 16  # bits in the port expander to hit
        with Pcb(self.pcb_host, timeout=self.pcb_timeout, expected_muxes=em) as p:
            for repeat in range(n_repeats):
                ret, found_prompt = p.query_nocheck("s")  # deselect
                self.assertTrue(found_prompt)
                self.assertIsInstance(ret, str)
                self.assertEqual(len(ret), 0)

                for slot in em:
                    for b in range(n_bits):
                        ret, found_prompt = p.query_nocheck(f"s{slot}{1<<b:05d}")  # hit each port expander line once
                        self.assertTrue(found_prompt)
                        self.assertIsInstance(ret, str)
                        self.assertEqual(len(ret), 0)

                    ret, found_prompt = p.query_nocheck(f"s{slot}0")  # deselect
                    self.assertTrue(found_prompt)
                    self.assertIsInstance(ret, str)
                    self.assertEqual(len(ret), 0)
