import unittest
from threading import Event as tEvent

from centralcontrol.fabric import Fabric


class FabricTestCase(unittest.TestCase):
    """testing for centralcontrol fabric"""

    def test_init(self):
        """test fabric initilization"""

        # event to inticate that a run should die prematurely
        killer = tEvent()

        f = Fabric(killer=killer)

        self.assertIsInstance(f, Fabric)

    def test_fake_instrument_connect(self):
        """checks that fake instruments can be connected and disconnected"""
        killer = tEvent()
        with Fabric(killer=killer) as f:
            con_args = {}
            con_args["pcb_virt"] = True
            con_args["motion_virt"] = True

            f.connect_instruments(**con_args)
