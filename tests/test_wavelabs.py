import unittest

from centralcontrol.wavelabs import Wavelabs


class WavelabsTestCase(unittest.TestCase):
    """testing for wavelabs solar sim control code"""

    def test_relay_init(self):
        """needs relay server service running and actual hardware to pass"""
        default_recipe = "am1_5_1_sun"
        wl = Wavelabs(host="127.0.0.1", port=3335, relay=True, default_recipe="am1_5_1_sun")  #  for comms via relay
        self.assertIsInstance(wl, Wavelabs)
