import unittest

from centralcontrol.illumination import Illumination


class IlluminationTestCase(unittest.TestCase):
    """testing for high level Illumination object"""

    protocol = "wavelabs-relay"
    # host = "127.0.0.1"
    host = "10.56.0.4"
    port = 3335
    connection_timeout = 10
    comms_timeout = 1
    recipe = "am1_5_1_sun"

    def test_init(self):
        """class initilization test"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        self.assertIsInstance(ill, Illumination)

    def test_connect(self):
        """class connection test"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        return_code = ill.connect()
        self.assertEqual(return_code, 0)
        ill.disconnect()

    def test_set_recipe(self):
        """class connection test"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        return_code = ill.connect()
        self.assertEqual(return_code, 0)
        return_code = ill.set_recipe(recipe_name=self.recipe)
        self.assertEqual(return_code, 0)
        # ill.disconnect()

    def test_get_run_status(self):
        """status read test"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        return_code = ill.connect()
        self.assertEqual(return_code, 0)
        status = ill.get_run_status()
        self.assertIsInstance(status, str)
        self.assertIn(status, ("running", "finished"))
        print(f"ill get_run_status() complete with {status=}")
        ill.disconnect()

    def test_get_temperatures(self):
        """temperature fetching test"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        return_code = ill.connect()
        self.assertEqual(return_code, 0)
        temp = ill.get_temperatures()
        self.assertIsInstance(temp, list)
        self.assertEqual(len(temp), 2)
        self.assertIsInstance(temp[0], float)
        self.assertIsInstance(temp[1], float)
        print(f"ill get_temperatures() complete with {temp=}")
        ill.disconnect()
