import paho.mqtt.client as mqtt
import pickle
import time

import yaml

timestamp = time.time()

args = {
    "ad_switch": True,
    "chan1": 0.0,
    "chan1_ma": 0.0,
    "chan2": 0.0,
    "chan2_ma": 0.0,
    "chan3": 0.0,
    "chan3_ma": 0.0,
    "eqe_bias": 0.0,
    "eqe_devs": "0x00000003FFC0",
    "eqe_end": 1100.0,
    "eqe_int": 10,
    "eqe_selections": [
        "sb1",
        "sb2",
        "sb3",
        "sb4",
        "sb5",
        "sb6",
        "sc1",
        "sc2",
        "sc3",
        "sc4",
        "sc5",
        "sc6",
    ],
    "eqe_start": 300.0,
    "eqe_step": 100.0,
    "eqe_subs_dev_nums": [1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6],
    "eqe_subs_labels": [
        "bad devices",
        "bad devices",
        "bad devices",
        "bad devices",
        "bad devices",
        "bad devices",
        "C",
        "C",
        "C",
        "C",
        "C",
        "C",
    ],
    "eqe_subs_names": ["B", "B", "B", "B", "B", "B", "C", "C", "C", "C", "C", "C"],
    "goto_x": 62.5,
    "goto_y": 0.0,
    "goto_z": 0.0,
    "i_dwell": 3.0,
    "i_dwell_check": True,
    "i_dwell_value": 0.0,
    "i_dwell_value_ma": 0.0,
    "iv_devs": "0x000000000FFF",
    "iv_selections": [
        "sa1",
        "sa2",
        "sa3",
        "sa4",
        "sa5",
        "sa6",
        "sb1",
        "sb2",
        "sb3",
        "sb4",
        "sb5",
        "sb6",
    ],
    "iv_steps": 101.0,
    "iv_subs_dev_nums": [1, 2, 3, 4, 5, 6, 1, 2, 3, 4, 5, 6],
    "iv_subs_labels": [
        "A",
        "A",
        "A",
        "A",
        "A",
        "A",
        "bad devices",
        "bad devices",
        "bad devices",
        "bad devices",
        "bad devices",
        "bad devices",
    ],
    "iv_subs_names": ["A", "A", "A", "A", "A", "A", "B", "B", "B", "B", "B", "B"],
    "label_tree": ["A", "bad devices", "C", "D", "E", "F", "G", "H"],
    "light_recipe": "AM1.5_1.0SUN",
    "lit_sweep": 0,
    "mppt_check": True,
    "mppt_dwell": 10.0,
    "mppt_params": "basic://",
    "nplc": 1.0,
    "return_switch": True,
    "run_name": "1595938312",
    "run_name_prefix": "",
    "run_name_suffix": "1595938312",
    "smart_mode": False,
    "source_delay": 3.0,
    "subs_names": ["A", "B", "C", "D", "E", "F", "G", "H"],
    "sweep_check": True,
    "sweep_end": -0.2,
    "sweep_start": 1.2,
    "v_dwell": 3.0,
    "v_dwell_check": True,
    "v_dwell_value": 0.0,
}
args["dummy"] = True


def yaml_include(loader, node):
    """Load tagged yaml files into root file."""
    with open(node.value) as f:
        return yaml.load(f, Loader=yaml.FullLoader)


# bind include function to !include tags in yaml config file
yaml.add_constructor("!include", yaml_include)


def load_config_from_file():
    """Load the configuration file into memory."""
    # try to load the configuration file from the current working directory
    with open("example_config.yaml", "r") as f:
        config = yaml.load(f, Loader=yaml.FullLoader)

    return config


# config = load_config_from_file()
config = {
    "controller": {"uri": "127.0.0.1"},
    "ivt": {"percent_beyond_voc": 25, "voltage_beyond_isc": 0.1},
    "lia": {
        "address": "127.0.0.1",
        "baud": 9600,
        "output_interface": 0,
        "terminator": "\\r",
    },
    "monochromator": {
        "address": "127.0.0.1",
        "baud": 9600,
        "filter_change_wls": [370, 640, 715, 765],
        "grating_change_wls": [1200],
        "terminator": "\\r",
    },
    "network": {"archive": "ftp://test:21/drop", "live_data_uri": "https://google.com"},
    "psu": {
        "address": "127.0.0.1",
        "baud": 9600,
        "calibration": {"current_step": 0.1, "max_current": 1},
        "ch1_voltage": 30,
        "ch2_voltage": 30,
        "ch3_voltage": 5,
        "terminator": "\\r",
    },
    "reference": {
        "calibration": {
            "eqe": {
                "eqe": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
                "wls": [
                    350,
                    400,
                    450,
                    500,
                    550,
                    600,
                    650,
                    700,
                    750,
                    800,
                    850,
                    900,
                    950,
                    1000,
                    1050,
                    1100,
                ],
            }
        },
        "spectra": {"AM1.5G": {"irr": [1, 1, 1], "wls": [0, 1, 2]}},
    },
    "smu": {
        "address": "127.0.0.1",
        "baud": 57600,
        "front_terminals": False,
        "terminator": "\\n",
        "two_wire": False,
    },
    "solarsim": {"uri": "wavelabs://127.0.0.1:1111"},
    "stage": {
        "uri": "127.0.0.1",
        "custom_positions": {
            "Load Position": 23,
            "Midway": 62.5,
            "Offline Position": 120,
            "Test spot A": 88.54241,
        },
        "experiment_positions": {"eqe": [800, 200], "solarsim": [200, 200]},
        "length": [850, 350],
        "speed": 29,
    },
    "substrates": {
        "active_layout": "6px_1in",
        "adapters": {
            "6px_1in_pcb": {"pcb_contact_pads": 6, "pcb_resistor": 0},
            "6px_30mm_pcb": {"pcb_contact_pads": 6, "pcb_resistor": 0},
        },
        "layout_names": ["6px_1in"],
        "layouts": {
            "4px_30mm": {
                "areas": [0.15, 0, 1.0, 0.15, 0.15, 0],
                "pcb_name": "6px_30mm_pcb",
                "pixels": [1, 3, 4, 5],
                "positions": [[-6, 1], [0, 1], [0, 1], [0, 1], [6, 1], [0, 1]],
            },
            "6px_1in": {
                "areas": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
                "pcb_name": "6px_1in_pcb",
                "pixels": [1, 2, 3, 4, 5, 6],
                "positions": [[-5, 2], [-5, 2], [0, 3], [0, 4], [5, 5], [5, 6]],
            },
        },
        "number": [2, 4],
        "spacing": [35, 30],
    },
    "visa": {"visa_lib": "@py"},
}


def test_saver():
    """Test the saver client."""
    run_payload = {
        "args": {"destination": f"{timestamp}_test_data"},
        "config": {"test": "test"},
    }

    raw_ivt_data = [1, 1, 1, 1]
    raw_ivt_payload = {
        "data": raw_ivt_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "light",
        "pixel": {"area": 1},
    }

    raw_iv_data = [[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]]
    raw_iv_payload = {
        "data": raw_iv_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "light",
        "pixel": {"area": 1},
    }

    raw_eqe_data = [1] * 14
    raw_eqe_payload = {
        "data": raw_eqe_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "",
        "pixel": {"area": 1},
    }

    processed_ivt_data = [1, 1, 1, 1, 2, 2]
    processed_ivt_payload = {
        "data": processed_ivt_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "light",
        "pixel": {"area": 1},
    }

    processed_iv_data = [[1, 1, 1, 1, 2, 2], [1, 1, 1, 1, 2, 2], [1, 1, 1, 1, 2, 2]]
    processed_iv_payload = {
        "data": processed_iv_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "light",
        "pixel": {"area": 1},
    }

    processed_eqe_data = [1] * 15
    processed_eqe_payload = {
        "data": processed_eqe_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "",
        "pixel": {"area": 1},
    }

    cal_eqe_data = [raw_eqe_data, raw_eqe_data, raw_eqe_data]
    cal_eqe_payload = {"data": cal_eqe_data, "diode": "test", "timestamp": timestamp}

    cal_spectrum_data = [[1, 1], [1, 1], [1, 1]]
    cal_spectrum_payload = {
        "data": cal_spectrum_data,
        "timestamp": timestamp,
    }

    cal_solarsim_diode_data = [raw_ivt_data, raw_ivt_data, raw_ivt_data]
    cal_solarsim_diode_payload = {
        "data": cal_solarsim_diode_data,
        "diode": "test",
        "timestamp": timestamp,
    }

    cal_rtd_data = cal_solarsim_diode_data
    cal_rtd_payload = {
        "data": cal_rtd_data,
        "diode": "test",
        "timestamp": timestamp,
    }

    cal_psu_data = [[1, 1, 1, 1, 1], [1, 1, 1, 1, 1], [1, 1, 1, 1, 1]]
    cal_psu_payload = {
        "data": cal_psu_data,
        "diode": "test",
        "timestamp": timestamp,
    }

    mqttc.publish("measurement/run", pickle.dumps(run_payload), 2).wait_for_publish()

    mqttc.publish(
        "data/raw/vt_measurement", pickle.dumps(raw_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/it_measurement", pickle.dumps(raw_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/mppt_measurement", pickle.dumps(raw_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/iv_measurement", pickle.dumps(raw_iv_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/eqe_measurement", pickle.dumps(raw_eqe_payload), 2
    ).wait_for_publish()

    mqttc.publish(
        "data/processed/vt_measurement", pickle.dumps(processed_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/processed/it_measurement", pickle.dumps(processed_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/processed/mppt_measurement", pickle.dumps(processed_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/processed/iv_measurement", pickle.dumps(processed_iv_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/processed/eqe_measurement", pickle.dumps(processed_eqe_payload), 2
    ).wait_for_publish()

    mqttc.publish(
        "calibration/eqe", pickle.dumps(cal_eqe_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "calibration/spectrum", pickle.dumps(cal_spectrum_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "calibration/solarsim_diode", pickle.dumps(cal_solarsim_diode_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "calibration/rtd", pickle.dumps(cal_rtd_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "calibration/psu/ch1", pickle.dumps(cal_psu_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "calibration/psu/ch2", pickle.dumps(cal_psu_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "calibration/psu/ch3", pickle.dumps(cal_psu_payload), 2
    ).wait_for_publish()


def test_analyser():
    run_payload = {
        "args": {"destination": f"{timestamp}_test_data"},
        "config": {
            "reference": {"calibration": {"eqe": {"wls": [0, 1, 2], "eqe": [2, 2, 2]}}}
        },
    }

    raw_ivt_data = [1, 1, 1, 1]
    raw_ivt_payload = {
        "data": raw_ivt_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "light",
        "pixel": {"area": 1},
    }

    raw_iv_data = [[1, 1, 1, 1], [2, 2, 2, 2], [3, 3, 3, 3]]
    raw_iv_payload = {
        "data": raw_iv_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "light",
        "pixel": {"area": 1},
    }

    raw_eqe_data = [1] * 14
    raw_eqe_payload = {
        "data": raw_eqe_data,
        "idn": "test",
        "clear": False,
        "end": False,
        "sweep": "",
        "pixel": {"area": 1},
    }

    cal_eqe_data = [[0 for i in raw_eqe_data], raw_eqe_data, [2 for i in raw_eqe_data]]
    cal_eqe_payload = {"data": cal_eqe_data, "diode": "test", "timestamp": timestamp}

    mqttc.publish(
        "calibration/eqe", pickle.dumps(cal_eqe_payload), 2
    ).wait_for_publish()

    mqttc.publish("measurement/run", pickle.dumps(run_payload), 2).wait_for_publish()

    mqttc.publish(
        "data/raw/vt_measurement", pickle.dumps(raw_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/it_measurement", pickle.dumps(raw_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/mppt_measurement", pickle.dumps(raw_ivt_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/iv_measurement", pickle.dumps(raw_iv_payload), 2
    ).wait_for_publish()
    mqttc.publish(
        "data/raw/eqe_measurement", pickle.dumps(raw_eqe_payload), 2
    ).wait_for_publish()


def test_run():
    mqttc.publish(
        "measurement/run", pickle.dumps({"args": args, "config": config}), 2
    ).wait_for_publish()


def test_stop():
    test_run()
    time.sleep(1)
    mqttc.publish(
        "measurement/stop", pickle.dumps({"args": args, "config": config}), 2
    ).wait_for_publish()


def test_cal_eqe():
    mqttc.publish(
        "measurement/calibrate_eqe", pickle.dumps({"args": args, "config": config}), 2
    ).wait_for_publish()


def test_cal_psu():
    mqttc.publish(
        "measurement/calibrate_psu", pickle.dumps({"args": args, "config": config}), 2
    ).wait_for_publish()


def test_cal_solarsim_diodes():
    mqttc.publish(
        "measurement/calibrate_solarsim_diodes",
        pickle.dumps({"args": args, "config": config}),
        2,
    ).wait_for_publish()


def test_cal_spectrum():
    mqttc.publish(
        "measurement/calibrate_spectrum",
        pickle.dumps({"args": args, "config": config}),
        2,
    ).wait_for_publish()


def test_contact_check():
    mqttc.publish(
        "measurement/contact_check", pickle.dumps({"args": args, "config": config}), 2,
    ).wait_for_publish()


def test_cal_rtd():
    mqttc.publish(
        "measurement/calibrate_rtd", pickle.dumps({"args": args, "config": config}), 2
    ).wait_for_publish()


def test_home():
    mqttc.publish(
        "measurement/home", pickle.dumps({"args": args, "config": config}), 2
    ).wait_for_publish()


def test_goto():
    mqttc.publish(
        "measurement/goto", pickle.dumps({"args": args, "config": config}), 2
    ).wait_for_publish()


def test_read():
    mqttc.publish(
        "measurement/read_stage", pickle.dumps({"args": args, "config": config}), 2
    ).wait_for_publish()


if __name__ == "__main__":
    mqttc = mqtt.Client()
    mqttc.connect("127.0.0.1")
    mqttc.loop_start()

    # test_saver()

    # test_analyser()

    # test_cal_eqe()

    # test_cal_psu()

    # test_cal_spectrum()

    # test_cal_solarsim_diodes()

    # test_cal_rtd()

    test_run()

    mqttc.loop_stop()
    mqttc.disconnect()
