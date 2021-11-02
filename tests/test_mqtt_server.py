import unittest
import collections

from centralcontrol.fabric import Fabric
from centralcontrol.mqtt_server import MQTTServer


class MqttServerTestCase(unittest.TestCase):
    """testing for centralcontrol mqtt_server"""

    def test_init(self):
        """test mqtt_server initilization"""

        m = MQTTServer()
        self.assertIsInstance(m, MQTTServer)

    def test_fake_ivt(self):
        """checks that a fake IV experiment can be run"""
        m = MQTTServer()
        with Fabric(killer=m.killer) as f:
            # form a fake pixel queue
            run_q = collections.deque()
            group_dict = {}
            pixel_dict = {}
            pixel_dict["label"] = "dog"
            pixel_dict["layout"] = "cat"
            pixel_dict["sub_name"] = "a"
            pixel_dict["device_label"] = "a1"
            pixel_dict["pixel"] = 1
            pixel_dict["pos"] = [30]
            pixel_dict["area"] = 1.0
            pixel_dict["mux_string"] = f"s{pixel_dict['sub_name']}{(1<< 8)+(1<<1)+(1<<0):05}"
            group_dict[0] = pixel_dict
            pixel_dict = {}
            pixel_dict["label"] = "bat"
            pixel_dict["layout"] = "bird"
            pixel_dict["sub_name"] = "b"
            pixel_dict["device_label"] = "b1"
            pixel_dict["pixel"] = 1
            pixel_dict["pos"] = [40]
            pixel_dict["area"] = 1.0
            pixel_dict["mux_string"] = f"s{pixel_dict['sub_name']}{(1<< 8)+(1<<1)+(1<<0):05}"
            group_dict[1] = pixel_dict
            run_q.append(group_dict)

            # makeup the request object
            request = {}

            # setup config with all faked/virtual instruments
            config = {}
            smu = []
            smu.append({"virtual": True})
            smu.append({"virtual": True})
            config["smu"] = smu
            config["controller"] = {"virtual": True}
            config["stage"] = {"virtual": True, "uri": "us://controller?el=375&spm=6400&hto=60"}
            config["solarsim"] = {"virtual": True}
            request["config"] = config

            # setup stuff relevant for this test that the gui would have provided
            args = {}

            args["nplc"] = -1
            args["cycles"] = 1
            args["jmax"] = 50
            args["imax"] = 100

            args["i_dwell"] = 1
            args["i_dwell_value"] = 0

            args["sweep_check"] = True
            args["return_switch"] = True
            args["lit_sweep"] = 0
            args["sweep_start"] = 1.2
            args["sweep_end"] = -1.2
            args["iv_steps"] = 101
            args["source_delay"] = 1

            args["mppt_dwell"] = 1
            args["mppt_params"] = "gd://"

            args["v_dwell"] = 1
            args["v_dwell_value"] = 0

            request["args"] = args

            # run the experiment
            m._ivt(run_q, request, f)
            f.disconnect_all_instruments()

        self.assertEqual(len(f._connected_instruments), 0)

    def test_full_cli_run(self):
        """just start a normal CLI run (runs forever)"""
        m = MQTTServer()
        m.run()
