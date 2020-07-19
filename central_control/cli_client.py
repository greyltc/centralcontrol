#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json

# for __version__
import central_control

import paho.mqtt.client as mqtt


def get_args():
    """Get CLI arguments and options."""
    parser = argparse.ArgumentParser(
        description="Automated solar cell I-V-t and EQE measurement."
    )

    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version="%(prog)s " + central_control.__version__,
    )
    parser.add_argument("-o", "--operator", type=str, help="Name of operator")
    parser.add_argument(
        "-r",
        "--run-description",
        type=str,
        help="Words describing the measurements about to be taken",
    )
    parser.add_argument(
        "-m", "--mqtthost", type=str, help="MQTT hostname or IP address",
    )

    measure = parser.add_argument_group(
        "optional arguments for measurement configuration"
    )
    measure.add_argument(
        "-d",
        "--experiment-folder",
        help="Directory name (relative) in which to save the output data",
    )
    measure.add_argument(
        "-a",
        "--iv-pixel-address",
        default=None,
        type=str,
        help="Hexadecimal bit mask for enabled pixels for I-V-t measurements",
    )
    measure.add_argument(
        "-b",
        "--eqe-pixel-address",
        default=None,
        type=str,
        help="Hexadecimal bit mask for enabled pixels for EQE measurements",
    )
    measure.add_argument(
        "-i",
        "--layouts",
        nargs="*",
        help="List of substrate layout names to use for finding pixel information from the configuration file",
    )
    measure.add_argument(
        "--labels", nargs="*", help="List of Substrate labels",
    )
    measure.add_argument(
        "--sweep-1", default=False, action="store_true", help="Do the first I-V sweep",
    )
    measure.add_argument(
        "--sweep-2", default=False, action="store_true", help="Do the second I-V sweep",
    )
    measure.add_argument(
        "--steadystate-v",
        type=float,
        default=0,
        help="Steady state value of V to measure I",
    )
    measure.add_argument(
        "--steadystate-i",
        type=float,
        default=0,
        help="Steady state value of I to measure V",
    )
    measure.add_argument(
        "--i-t",
        type=float,
        default=10.0,
        help="Number of seconds to measure to find steady state I@constant V",
    )
    measure.add_argument(
        "--v-t",
        type=float,
        default=10.0,
        help="Number of seconds to measure to find steady state V@constant I",
    )
    measure.add_argument(
        "--mppt-t",
        type=float,
        default=37.0,
        help="Do maximum power point tracking for this many seconds",
    )
    measure.add_argument(
        "--mppt-params",
        type=str,
        default="basic://7:10",
        help="Extra configuration parameters for the maximum power point tracker, see https://git.io/fjfrZ",
    )
    measure.add_argument(
        "--eqe", default=False, action="store_true", help="Do and EQE scan",
    )

    setup = parser.add_argument_group("optional arguments for setup configuration")
    setup.add_argument(
        "--light-recipe",
        type=str,
        default="AM1.5_1.0SUN",
        help="Recipe name for Wavelabs to load",
    )
    setup.add_argument(
        "--voltage-compliance-override",
        default=3,
        type=float,
        help="Override voltage complaince setting used during Voc measurement",
    )
    setup.add_argument(
        "--current-compliance-override",
        type=float,
        help="Override current compliance value used during I-V scans",
    )
    setup.add_argument(
        "--scan-start-override-1",
        type=float,
        help="Override the start sweep voltage limit for sweep-1",
    )
    setup.add_argument(
        "--scan-end-override-1",
        type=float,
        help="Override the end sweep voltage limit for sweep-1",
    )
    setup.add_argument(
        "--scan-start-override-2",
        type=float,
        help="Override the start sweep voltage limit for sweep-2",
    )
    setup.add_argument(
        "--scan-end-override-2",
        type=float,
        help="Override the end sweep voltage limit for sweep-2",
    )
    setup.add_argument(
        "--scan-points",
        type=int,
        default=101,
        help="Number of measurement points in I-V curve",
    )
    setup.add_argument(
        "--scan-nplc",
        type=float,
        default=1,
        help="Sourcemeter NPLC setting to use during I-V scans",
    )
    setup.add_argument(
        "--steadystate-nplc",
        type=float,
        default=1,
        help="Sourcemeter NPLC setting to use during steady-state scans and max power point tracking",
    )
    setup.add_argument(
        "--scan-step-delay",
        type=float,
        default=-1,
        help="Sourcemeter settling delay in seconds to use during I-V scans. -1 = auto",
    )
    setup.add_argument(
        "--steadystate-step-delay",
        type=float,
        default=-1,
        help="Sourcemeter settling delay in seconds to use during steady-state scans and max power point tracking. -1 = auto",
    )
    setup.add_argument(
        "--home",
        default=False,
        action="store_true",
        help="Home the stage along each axis",
    )
    setup.add_argument(
        "--read-stage",
        default=False,
        action="store_true",
        help="Read the current stage position",
    )
    setup.add_argument(
        "--goto",
        type=float,
        nargs="+",
        default=None,
        help="Go to stage position. Input is a list of positions in steps along each available axis in order",
    )
    setup.add_argument(
        "--calibrate-eqe",
        default=False,
        action="store_true",
        help="Measure spectral response of reference photodiode",
    )
    setup.add_argument(
        "--eqe-integration-time",
        type=int,
        default=8,
        help="Lock-in amplifier integration time setting (integer corresponding to a time)",
    )
    setup.add_argument(
        "--eqe-smu-v",
        type=float,
        default=0,
        help="Sourcemeter bias voltage during EQE scan",
    )
    setup.add_argument(
        "--eqe-start-wl",
        type=float,
        default=350,
        help="Starting wavelength for EQE scan in nm",
    )
    setup.add_argument(
        "--eqe-end-wl",
        type=float,
        default=1100,
        help="End wavelength for EQE scan in nm",
    )
    setup.add_argument(
        "--eqe-num-wls",
        type=float,
        default=76,
        help="Number of wavelegnths to measure in EQE scan",
    )
    setup.add_argument(
        "--eqe-repeats",
        type=int,
        default=1,
        help="Number of repeat measurements at each wavelength",
    )
    setup.add_argument(
        "--eqe-autogain-off",
        default=False,
        action="store_true",
        help="Disable automatic gain setting",
    )
    setup.add_argument(
        "--eqe-autogain-method",
        type=str,
        default="user",
        help="Method of automatically establishing gain setting",
    )
    setup.add_argument(
        "--calibrate-psu",
        default=False,
        action="store_true",
        help="Calibrate PSU current to LEDs measuring reference photodiode",
    )
    setup.add_argument(
        "--calibrate-psu-ch",
        type=int,
        default=1,
        help="PSU channel to calibrate: 1, 2, or 3",
    )
    setup.add_argument(
        "--psu-is",
        type=float,
        nargs=3,
        default=[0, 0, 0],
        help="LED PSU channel currents (A)",
    )
    setup.add_argument(
        "--calibrate-solarsim",
        default=False,
        action="store_true",
        help="Read diode ADC counts now and store those as corresponding to 1.0 sun intensity",
    )
    setup.add_argument(
        "--contact-check",
        default=False,
        action="store_true",
        help="Cycle through pixels checking whether force and sense pins are connected",
    )

    testing = parser.add_argument_group("optional arguments for debugging/testing")
    testing.add_argument(
        "--dummy",
        default=False,
        action="store_true",
        help="Run in dummy mode (doesn't need sourcemeter, generates simulated device data)",
    )
    testing.add_argument(
        "--scan-visa",
        default=False,
        action="store_true",
        help="Scan for obvious VISA resource names, print them and exit",
    )
    testing.add_argument(
        "--test-hardware",
        default=False,
        action="store_true",
        help="Exercises all the hardware, used to check for and debug issues",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = get_args()

    if args.home is True:
        action = "home"
    elif args.read_stage is True:
        action = "read_stage"
    elif args.goto is True:
        action = "goto"
    elif args.calibrate_solarsim is True:
        action = "calibrate_solarsim"
    elif args.calibrate_eqe is True:
        action = "calibrate_eqe"
    elif args.calibrate_psu is True:
        action = "calibrate_psu"
    elif args.contact_check is True:
        action = "contact_check"
    elif (
        (args.v_t > 0)
        or (args.i_t > 0)
        or (args.sweep1 is True)
        or (args.sweep2 is True)
        or (args.mppt_t > 0)
        or (args.eqe is True)
    ):
        action = "run"

    payload = json.dumps({"action": action, "data": vars(args)})

    mqttc = mqtt.Client()
    mqttc.connect(args.mqtthost)
    mqttc.publish("server/request", payload, qos=2).wait_for_publish()
