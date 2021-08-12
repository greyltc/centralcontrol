import unittest
import socket
import time

from centralcontrol.wavelabs import Wavelabs


class WavelabsTestCase(unittest.TestCase):
    """testing for wavelabs solar sim control code"""

    def test_relay_init(self):
        default_recipe = "am1_5_1_sun"
        relay_host = "127.0.0.1"
        relay_port = 3335
        use_relay = True
        wl = Wavelabs(host=relay_host, port=relay_port, relay=use_relay, default_recipe=default_recipe)
        self.assertIsInstance(wl, Wavelabs)

    def test_relay_connect(self):
        """needs relay server service running and actual hardware with correct recipe name to pass"""
        default_recipe = "am1_5_1_sun"
        relay_host = "127.0.0.1"
        relay_port = 3335
        use_relay = True
        wl = Wavelabs(host=relay_host, port=relay_port, relay=use_relay, default_recipe=default_recipe)
        wl.connect()
        self.assertIsInstance(wl.connection, socket.socket)
        del wl

    def test_relay_on_off(self):
        """needs relay server service running and actual hardware with correct recipe name to pass"""
        default_recipe = "am1_5_1_sun"
        relay_host = "127.0.0.1"
        relay_port = 3335
        use_relay = True
        wl = Wavelabs(host=relay_host, port=relay_port, relay=use_relay, default_recipe=default_recipe)
        wl.connect()
        runID = wl.on()
        self.assertIsInstance(runID, str)
        self.assertTrue(runID.startswith("sn"))
        time.sleep(1)
        self.assertEqual(wl.off(), 0)
        del wl

    def test_relay_on_off_repeat(self):
        """needs relay server service running and actual hardware with correct recipe name to pass"""
        default_recipe = "am1_5_1_sun"
        relay_host = "127.0.0.1"
        relay_port = 3335
        use_relay = True
        wl = Wavelabs(host=relay_host, port=relay_port, relay=use_relay, default_recipe=default_recipe)
        wl.connect()
        repeats = 100
        for i in range(repeats):
            runID = wl.on()
            self.assertIsInstance(runID, str)
            self.assertTrue(runID.startswith("sn"))
            time.sleep(0.25)
            self.assertEqual(wl.off(), 0)
            time.sleep(0.25)
        del wl
