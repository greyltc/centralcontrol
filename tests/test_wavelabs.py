import unittest
import socket
import time

from centralcontrol.wavelabs import Wavelabs


class WavelabsTestCase(unittest.TestCase):
    """testing for wavelabs solar sim control code"""

    host = "127.0.0.1"
    port = 3335
    use_relay = True
    recipe = "am1_5_1_sun"

    def test_init(self):
        wl = Wavelabs(host=self.host, port=self.port, relay=self.use_relay)
        self.assertIsInstance(wl, Wavelabs)

    def test_connect(self):
        """needs relay server service running and actual hardware"""
        wl = Wavelabs(host=self.host, port=self.port, relay=self.use_relay)
        ret_val = wl.connect()
        self.assertEqual(0, ret_val)
        wl.disconnect()

    def test_set_recipe(self):
        """needs relay server service running and actual hardware with correct recipe name to pass"""
        wl = Wavelabs(host=self.host, port=self.port, relay=self.use_relay)
        ret_val = wl.connect()
        self.assertEqual(0, ret_val)
        ret_val2 = wl.activate_recipe(self.recipe)
        self.assertEqual(0, ret_val2)
        wl.disconnect()

    def test_on_off(self):
        """needs relay server service running and actual hardware with correct recipe name to pass"""
        wl = Wavelabs(host=self.host, port=self.port, relay=self.use_relay)
        ret_val = wl.connect()
        self.assertEqual(0, ret_val)
        ret_val2 = wl.activate_recipe(self.recipe)
        self.assertEqual(0, ret_val2)
        self.assertEqual(0, ret_val2)
        runID = wl.on()
        self.assertIsInstance(runID, str)
        self.assertTrue(runID.startswith("sn"))
        runID = wl.on()
        self.assertIsNone(runID)
        time.sleep(1)
        self.assertEqual(wl.off(), 0)
        self.assertEqual(wl.off(), 0)
        wl.disconnect()

    def test_on_off_repeat(self):
        """needs relay server service running and actual hardware with correct recipe name to pass"""
        wl = Wavelabs(host=self.host, port=self.port, relay=self.use_relay)
        ret_val = wl.connect()
        self.assertEqual(0, ret_val)
        ret_val2 = wl.activate_recipe(self.recipe)
        self.assertEqual(0, ret_val2)
        repeats = 100
        for i in range(repeats):
            runID = wl.on()
            self.assertIsInstance(runID, str)
            self.assertTrue(runID.startswith("sn"))
            time.sleep(0.25)
            self.assertEqual(wl.off(), 0)
            time.sleep(0.25)
        wl.disconnect()

    def test_spam_comms(self):
        """
        needs relay server service running and actual hardware with correct recipe name to pass
        spams recipe intensity read operation, assumes intensity is set to "100"
        """

        wl = Wavelabs(host=self.host, port=self.port, relay=self.use_relay)
        ret_val = wl.connect()
        self.assertEqual(0, ret_val)
        ret_val2 = wl.activate_recipe(self.recipe)
        self.assertEqual(0, ret_val2)
        repeats = 1000
        expected_intensity = "100"
        for i in range(repeats):
            self.assertEqual(wl.getRecipeParam(recipe_name=self.recipe, param="Intensity"), expected_intensity)
        wl.disconnect()

    def test_spectrum_fetch(self):
        """needs relay server service running and actual hardware with correct recipe name to pass"""

        wl = Wavelabs(host=self.host, port=self.port, relay=self.use_relay)
        ret_val = wl.connect()
        self.assertEqual(0, ret_val)
        ret_val2 = wl.activate_recipe(self.recipe)
        self.assertEqual(0, ret_val2)
        spectral_data = wl.get_spectrum()
        wl.disconnect()
        self.assertEqual(len(spectral_data), 2)
        self.assertIsInstance(spectral_data[0], list)
        self.assertIsInstance(spectral_data[1], list)
        self.assertGreater(len(spectral_data[0]), 0)
        self.assertGreater(len(spectral_data[1]), 0)
        self.assertIsInstance(spectral_data[0][0], float)
        self.assertIsInstance(spectral_data[1][0], float)
