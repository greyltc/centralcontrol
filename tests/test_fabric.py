import unittest
import multiprocessing

from centralcontrol.fabric import Fabric


class FabricTestCase(unittest.TestCase):
    """testing for centralcontrol fabric"""

    def test_init(self):
        """test fabric initilization"""

        # event to inticate that a run should die prematurely
        killer = multiprocessing.Event()

        f = Fabric(killer=killer)

        self.assertIsInstance(f, Fabric)

    def test_fake_instrument_connect(self):
        """checks that fake instruments can be connected and disconnected"""
        killer = multiprocessing.Event()
        with Fabric(killer=killer) as f:
            con_args = {}
            con_args["pcb_virt"] = True
            con_args["motion_virt"] = True
            con_args["light_virt"] = True
            con_args["lia_virt"] = True
            con_args["mono_virt"] = True
            con_args["psu_virt"] = True
            smus = []
            smus.append({"virtual": True})
            smus.append({"virtual": True})
            con_args["smus"] = smus

            f.connect_instruments(**con_args)
            self.assertEqual(len(f._connected_instruments), 6)
        self.assertEqual(len(f._connected_instruments), 0)
