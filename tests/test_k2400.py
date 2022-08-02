import unittest

import centralcontrol.k24002 as k2400


class K2400TestCase(unittest.TestCase):
    """testing for k2400 API"""

    def setUp(self):
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
        self.args = (address,)

        self.kwargs = {"two_wire": False}

    def test_init(self):
        """initilization test"""
        sm = k2400.k2400("")
        self.assertIsInstance(sm, k2400.k2400)

    def test_connect(self):
        """tests connection. needs real hardware"""

        with k2400.k2400(*self.args) as sm:
            self.assertTrue(sm.connected)
        self.assertTrue(sm.expect_in_idn in sm.idn)
        self.assertFalse(sm.connected)

    def test_dc_resistance(self):
        """
        tests making a resistance measurement with DC setup.
        needs real hardware
        """
        with k2400.k2400(*self.args) as sm:
            sm.setupDC(auto_ohms=True)
            rslt = sm.measure()
        self.assertIsInstance(rslt, list)
        self.assertIsInstance(rslt[0], tuple)
        self.assertEqual(5, len(rslt[0]))
        # print(f"R = {rslt[0][2]}")

    def test_contact_check(self):
        """tests contact check. needs real hardware"""

        with k2400.k2400(*self.args, **self.kwargs) as sm:
            sm.set_ccheck_mode(True)
            rslt = sm.contact_check()
            # print(f"Contact check result: {rslt}")
            sm.set_ccheck_mode(False)
        self.assertIsInstance(rslt, bool)

    def test_measure_until(self):
        """tests measure_until. needs real hardware"""
        seconds = 10  # [s]
        with k2400.k2400(*self.args) as sm:
            sm.setupDC(auto_ohms=True)
            sm.setNPLC(1)
            rslt = sm.measureUntil(t_dwell=seconds)
        self.assertIsInstance(rslt, list)
        self.assertIsInstance(rslt[-1], tuple)

    def test_sweep(self):
        """tests sweep. needs real hardware"""
        n_points = 101
        v_start = 1
        v_end = 0
        i_limit = 0.001
        with k2400.k2400(*self.args) as sm:
            sm.setupSweep(compliance=i_limit, nPoints=n_points, start=v_start, end=v_end)
            rslt = sm.measure(n_points)
        self.assertIsInstance(rslt, list)
        self.assertIsInstance(rslt[-1], tuple)
