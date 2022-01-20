import unittest

from centralcontrol.illumination import Illumination


class IlluminationTestCase(unittest.TestCase):
    """testing for high level Illumination object"""

    protocol = "wavelabs-relay"
    # host = "127.0.0.1"
    host = "10.56.0.4"
    port = 3335
    default_recipe = "am1_5_1_sun"
    connection_timeout = 10

    def test_init(self):
        """class initilization test"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, default_recipe=self.default_recipe, connection_timeout=self.connection_timeout)
        self.assertIsInstance(ill, Illumination)

    def test_connect(self):
        """class connection test"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, default_recipe=self.default_recipe, connection_timeout=self.connection_timeout)
        return_code = ill.connect()
        self.assertEqual(return_code, 0)
        ill.disconnect()

    def test_get_temperatures(self):
        """class connection test"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, default_recipe=self.default_recipe, connection_timeout=self.connection_timeout)
        return_code = ill.connect()
        self.assertEqual(return_code, 0)
        temp = ill.get_temperatures()
        self.assertEqual(len(temp), 2)
        self.assertIsInstance(temp[0], float)
        self.assertIsInstance(temp[1], float)
        print(f"ill get_temperatures() complete with {temp=}")
        ill.disconnect()
