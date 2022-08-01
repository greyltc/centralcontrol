import unittest

import centralcontrol.k24002 as k2400


class K2400TestCase(unittest.TestCase):
    """testing for k2400 API"""

    def test_init(self):
        """initilization test"""
        sm = k2400.K2400("")
        self.assertIsInstance(sm, k2400.K2400)

    def test_connect(self):
        """tests connection. needs real hardware"""
        schema = "hw://"
        port = "/dev/ttyS0"
        options = {}
        options["baudrate"] = 57600
        options["bytesize"] = "EIGHTBITS"
        options["parity"] = "PARITY_NONE"
        options["stopbits"] = "STOPBITS_ONE"
        options["timeout"] = 1
        options["xonxoff"] = True
        options["rtscts"] = False
        options["dsrdtr"] = False
        options["write_timeout"] = 1
        options["inter_byte_timeout"] = 1
        address = f"{schema}{port}?{'&'.join([f'{a}={b}' for a, b in options.items()])}"
        address = "socket://10.45.0.135:5025"
        args = (address,)
        with k2400.K2400(*args) as sm:
            self.assertTrue(sm.connected)
            sm.write("output on")
        self.assertFalse(sm.connected)
