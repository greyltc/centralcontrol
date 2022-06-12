import unittest

import centralcontrol.sourcemeter as sourcemeter


class SourcemeterTestCase(unittest.TestCase):
    """testing for Sourcemeter API"""

    cfg = {
        "enabled": True,
        "virtual": True,
    }

    def test_init(self):
        """factory and initilization tests"""
        smuc = sourcemeter.factory(self.cfg)  # use the factory to set up the class
        sm = smuc(**self.cfg)
        self.assertIsInstance(sm, sourcemeter.SourcemeterAPI)

    def test_connection(self):
        """test connect and disconnect calls"""
        smuc = sourcemeter.factory(self.cfg)  # use the factory to set up the class
        sm = smuc(**self.cfg)
        self.assertEqual(sm.connect(), 0)
        self.assertIsInstance(sm.idn, str)
        self.assertEqual(sm.disconnect(), None)

    def test_context(self):
        """test context usage"""
        smuc = sourcemeter.factory(self.cfg)  # use the factory to set up the class
        with smuc(**self.cfg) as sm:
            self.assertIsInstance(sm.idn, str)
