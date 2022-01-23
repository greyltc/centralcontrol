import unittest

import threading

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
        del ill
        self.assertEqual(return_code, 0)

    def test_set_recipe(self):
        """class connection test"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        return_code = ill.connect()
        self.assertEqual(return_code, 0)
        return_code = ill.set_recipe(recipe_name=self.recipe)
        del ill
        self.assertEqual(return_code, 0)

    def test_get_run_status(self):
        """status read test"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        return_code = ill.connect()
        self.assertEqual(return_code, 0)
        status = ill.get_run_status()
        del ill
        self.assertIsInstance(status, str)
        self.assertIn(status, ("running", "finished"))
        print(f"ill get_run_status() complete with {status=}")

    def test_state_change_force(self):
        """test forced light state change"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        ill.connect()
        ill.get_run_status()
        ill.set_recipe(recipe_name=self.recipe)

        light_on = True
        ill.set_state(force_state=light_on)
        self.assertEqual(ill.on, light_on)
        status = ill.get_run_status()
        self.assertEqual(ill.on, light_on)
        self.assertEqual(status, "running")

        light_on = True
        ill.set_state(force_state=light_on)
        self.assertEqual(ill.on, light_on)
        status = ill.get_run_status()
        self.assertEqual(ill.on, light_on)
        self.assertEqual(status, "running")

        light_on = False
        ill.set_state(force_state=light_on)
        self.assertEqual(ill.on, light_on)
        status = ill.get_run_status()
        self.assertEqual(ill.on, light_on)
        self.assertEqual(status, "finished")

        light_on = False
        ill.set_state(force_state=light_on)
        self.assertEqual(ill.on, light_on)
        status = ill.get_run_status()
        self.assertEqual(ill.on, light_on)
        self.assertEqual(status, "finished")

    def test_state_change(self):
        """test light state change with sync barrier height = 1"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        ill.connect()
        ill.get_run_status()
        ill.set_recipe(recipe_name=self.recipe)

        light_on = True
        ill.on = light_on
        self.assertEqual(ill.on, light_on)
        status = ill.get_run_status()
        self.assertEqual(ill.on, light_on)
        self.assertEqual(status, "running")

        light_on = True
        ill.on = light_on
        self.assertEqual(ill.on, light_on)
        status = ill.get_run_status()
        self.assertEqual(ill.on, light_on)
        self.assertEqual(status, "running")

        light_on = False
        ill.on = light_on
        self.assertEqual(ill.on, light_on)
        status = ill.get_run_status()
        self.assertEqual(ill.on, light_on)
        self.assertEqual(status, "finished")

        light_on = False
        ill.on = light_on
        self.assertEqual(ill.on, light_on)
        status = ill.get_run_status()
        self.assertEqual(ill.on, light_on)
        self.assertEqual(status, "finished")

    def test_state_change(self):
        """test light state change with thread sync barrier height = n"""
        sync_barrier_height = 2  # must be int, 2 or more

        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        ill.connect()
        ill.get_run_status()
        ill.set_recipe(recipe_name=self.recipe)
        ill.n_sync = sync_barrier_height

        # make a function for each thread to call
        def light_setter(on):
            ill.on = on

        # let's make sure we start with the light off
        light_on = False
        ill.set_state(force_state=light_on)
        self.assertEqual(ill.on, light_on)
        status = ill.get_run_status()
        self.assertEqual(ill.on, light_on)
        self.assertEqual(status, "finished")

        # repeat this test a number of times
        repeats = 5

        for repeat in range(repeats):
            # step 1
            # init threads into a master list
            light_on = True
            threads = [threading.Thread(target=light_setter, name=f"thread{i}", args=(light_on,)) for i in range(sync_barrier_height)]

            # start all but one thread
            late_start_thread = threads.pop()
            for thread in threads:
                thread.start()

            # check that the light is still off
            self.assertEqual(ill.on, False)
            status = ill.get_run_status()
            self.assertEqual(ill.on, False)
            self.assertEqual(status, "finished")

            # start the final thread (should pass the barrier now)
            late_start_thread.start()

            # put the late start thread back in the master list
            threads.append(late_start_thread)

            # wait for all the threads to complete
            for thread in threads:
                thread.join()

            # check that the light is now on
            self.assertEqual(ill.on, light_on)
            status = ill.get_run_status()
            self.assertEqual(ill.on, light_on)
            self.assertEqual(status, "running")

            # step 2
            # init threads into a master list
            light_on = True
            threads = [threading.Thread(target=light_setter, name=f"thread{i}", args=(light_on,)) for i in range(sync_barrier_height)]

            # these should blast right through because we're not changing state
            for thread in threads:
                thread.start()

            # wait for all the threads to complete
            for thread in threads:
                thread.join()

            # check that the light is still on
            self.assertEqual(ill.on, light_on)
            status = ill.get_run_status()
            self.assertEqual(ill.on, light_on)
            self.assertEqual(status, "running")

            # step 3
            # init threads into a master list
            light_on = False
            threads = [threading.Thread(target=light_setter, name=f"thread{i}", args=(light_on,)) for i in range(sync_barrier_height)]

            # start all but one thread
            late_start_thread = threads.pop()
            for thread in threads:
                thread.start()

            # check that the light is still on
            self.assertEqual(ill.on, True)
            status = ill.get_run_status()
            self.assertEqual(ill.on, True)
            self.assertEqual(status, "running")

            # start the final thread (should pass the barrier now)
            late_start_thread.start()

            # put the late start thread back in the master list
            threads.append(late_start_thread)

            # make sure all the threads are done
            for thread in threads:
                thread.join()

            # check that the light is now off
            self.assertEqual(ill.on, light_on)
            status = ill.get_run_status()
            self.assertEqual(ill.on, light_on)
            self.assertEqual(status, "finished")

            # step 4
            # init threads into a master list
            light_on = False
            threads = [threading.Thread(target=light_setter, name=f"thread{i}", args=(light_on,)) for i in range(sync_barrier_height)]

            # these should blast right through because we're not changing state
            for thread in threads:
                thread.start()

            # wait for all the threads to complete
            for thread in threads:
                thread.join()

            # check that the light is still off
            self.assertEqual(ill.on, light_on)
            status = ill.get_run_status()
            self.assertEqual(ill.on, light_on)
            self.assertEqual(status, "finised")

    def test_get_temperatures(self):
        """temperature fetching test (this is only expected to work after spectrum fetch)"""
        address = f"{self.protocol}://{self.host}:{self.port}"
        ill = Illumination(address=address, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        return_code = ill.connect()
        self.assertEqual(return_code, 0)
        temp = ill.get_temperatures()
        del ill
        self.assertIsInstance(temp, list)
        self.assertEqual(len(temp), 2)
        self.assertIsInstance(temp[0], float)
        self.assertIsInstance(temp[1], float)
        print(f"ill get_temperatures() complete with {temp=}")
