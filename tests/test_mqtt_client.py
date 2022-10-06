import unittest
import collections

from centralcontrol.fabric import Fabric
from centralcontrol.mqtt import MQTTClient


class MqttClientTestCase(unittest.TestCase):
    """testing for centralcontrol's mqtt.MQTTClient"""

    def test_init(self):
        """test MQTTClient initilization"""

        mc = MQTTClient()
        self.assertIsInstance(mc, MQTTClient)

    def test_fake_ivt(self):
        """checks that a fake IV experiment can be run"""
        mc = MQTTClient()
        with Fabric(killer=mc.killer) as f:
            # form a fake pixel queue
            run_q = collections.deque()
            group_dict = {}
            pixel_dict = {}
            pixel_dict["layout"] = "cat"
            pixel_dict["slot"] = "a"
            pixel_dict["device_label"] = "a1"
            pixel_dict["pad"] = 1
            pixel_dict["pos"] = [30]
            pixel_dict["area"] = 1.0
            pixel_dict["dark_area"] = 1.1
            pixel_dict["mux_sel"] = (pixel_dict["slot"], pixel_dict["pad"])
            group_dict[0] = pixel_dict
            pixel_dict = {}
            pixel_dict["layout"] = "bird"
            pixel_dict["slot"] = "b"
            pixel_dict["device_label"] = "b1"
            pixel_dict["pad"] = 1
            pixel_dict["pos"] = [40]
            pixel_dict["area"] = 1.0
            pixel_dict["dark_area"] = 1.1
            pixel_dict["mux_string"] = (pixel_dict["slot"], pixel_dict["pad"])
            group_dict[1] = pixel_dict
            run_q.append(group_dict)

            # makeup the request object
            request = {}

            # setup config with all faked/virtual instruments
            config = {}
            smu = []
            smu.append({"virtual": True, "address": "hw:///dev/ttyS0?baudrate=57600&bytesize=EIGHTBITS&parity=PARITY_NONE&stopbits=STOPBITS_ONE&timeout=1&xonxoff=True&rtscts=False&dsrdtr=False&write_timeout=1&inter_byte_timeout=1"})
            smu.append({"virtual": True, "address": "hw:///dev/ttyS1?baudrate=57600&bytesize=EIGHTBITS&parity=PARITY_NONE&stopbits=STOPBITS_ONE&timeout=1&xonxoff=True&rtscts=False&dsrdtr=False&write_timeout=1&inter_byte_timeout=1"})
            config["smus"] = smu
            config["mc"] = {"virtual": True}
            config["motion"] = {"virtual": True, "uri": "us://controller?el=375&spm=6400&hto=60"}
            config["solarsim"] = {"virtual": True}
            config["db"] = {"uri": "postgresql://"}
            config["setup"] = {"site": "The Moon", "name": "R&D testbed number two"}
            request["config"] = config

            # setup stuff relevant for this test that the gui would have provided
            args = {}

            args["user_name"] = "tester"
            args["run_name_prefix"] = "testing_"

            args["light_recipe"] = "stir in two cups of sugar"
            args["light_recipe_int"] = 100

            args["nplc"] = -1
            args["cycles"] = 1
            args["jmax"] = 50
            args["imax"] = 100

            args["i_dwell_check"] = True
            args["i_dwell"] = 1
            args["i_dwell_value"] = 0

            args["suns_voc"] = 10

            args["sweep_check"] = True
            args["return_switch"] = True
            args["lit_sweep"] = 0
            args["sweep_start"] = 1.2
            args["sweep_end"] = -1.2
            args["iv_steps"] = 101
            args["source_delay"] = 1

            args["mppt_check"] = True
            args["mppt_dwell"] = 1
            args["mppt_params"] = "gd://"

            args["v_dwell_check"] = True
            args["v_dwell"] = 1
            args["v_dwell_value"] = 0

            args["print_sweep_deets"] = True

            request["args"] = args

            # run the experiment
            mc._ivt(run_q, request, f)

    def test_full_cli_run(self):
        """just start a normal CLI run (runs forever)"""
        mc = MQTTClient()
        mc.run()
