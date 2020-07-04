"""Client for running the CLI based on MQTT messages."""

import json
import subprocess

import paho.mqtt.client as mqtt
import psutil


class CLIMQTT(mqtt.Client):
    """MQTT client that controls how the CLI is run from the GUI."""

    def __init__(self, MQTTHOST="127.0.0.1", topic="gui/#"):
        """Construct object.

        Connect the MQTT client to the broker, subscribe to the GUI topic, and create
        process attribute (for storing cli process).

        Parameters
        ----------
        MQTTHOST : str
            IP address or host name of the MQTT broker.
        topic : str
            Topic to subscribe to.
        """
        super().__init__()
        # connect MQTT client to broker
        self.connect(MQTTHOST)
        # subscribe to everything in the GUI topic
        self.subscribe(topic)

        # psutils process object
        self.proc = None

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context related to this object.

        Make sure everything gets cleaned up properly.
        """
        self._stop()
        self.loop_stop()
        self.disconnect()

    def on_message(self, mqttc, obj, msg):
        """Act on an MQTT message."""
        m = json.loads(msg.payload)

        # perform action depending on which button generated the message
        if (button := msg.topic.split("/")[-1]) == "run":
            self._run(m)
        elif button == "pause":
            self._pause()
        elif button == "stop":
            self._stop()
        elif button == "cal_eqe":
            self._cal_eqe(m)
        elif button == "cal_psu":
            self._cal_psu(m)
        elif button == "home":
            self._home()

    def _start_or_resume_subprocess(self, args):
        """Start or resume a subprocess.

        Start a new subprocess is there isn't one running or resume a subprocess if one
        is paused.

        Parameters
        ----------
        args : list
            List of command line arguments to parse to CLI.
        """
        # start process if there is none
        if self.proc is None:
            self._start_subprocess(args)
        else:
            try:
                # try to resume the process if it's paused
                if self.proc.status() == "stopped":
                    self.proc.resume()
                else:
                    pass
            except ProcessLookupError:
                # process was run but has finished so start a new one
                self._start_subprocess(args)

    def _start_subprocess(self, args):
        """Run the CLI as a subprocess.

        Parameters
        ----------
        args : list
            List of command line arguments to parse to CLI.
        """
        cli_args = ["python", "cli.py"] + args
        p = subprocess.Popen(cli_args)
        self.proc = psutil.Process(p.pid)

    def _format_run_msg(self, msg):
        """Convert run msg from GUI to CLI list for subprocess.

        Parameters
        ----------
        msg : dict
            Dictionary of settings sent from the GUI.

        Returns
        -------
        args : list
            List of command line arguments to parse to CLI.
        """
        # TODO: format run msg dict into args
        args = msg

        return args

    def _run(self, msg):
        """Run an experiment.

        Parameters
        ----------
        msg : dict
            Dictionary of settings sent from the GUI.
        """
        args = self._format_run_msg(msg)
        self._start_or_resume_subprocess(args)

    def _pause(self):
        """Pause a running subprocess."""
        # check if a process may still be running
        if self.proc is not None:
            try:
                # try to pause the process if it's running
                if self.proc.status() == "running":
                    self.proc.suspend()
            except ProcessLookupError:
                # process was run but has now finished
                self.proc = None
        else:
            pass

    def _stop(self):
        """Terminate a subprocess."""
        # check if a process may still be running
        if self.proc is not None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                # process was run but has now finished
                pass
            self.proc = None
        else:
            pass

    def _format_cal_eqe_msg(self, msg):
        """Convert calibrate EQE msg from GUI to CLI list for subprocess.

        Parameters
        ----------
        msg : dict
            Dictionary of settings sent from the GUI.

        Returns
        -------
        args : list
            List of command line arguments to parse to CLI.
        """
        # TODO: format run msg dict into args
        args = msg

        return args

    def _cal_eqe(self, msg):
        """Measure the EQE reference photodiode."""
        args = self._format_cal_eqe_msg(msg)
        self._start_or_resume_subprocess(args)

    def _format_cal_psu_msg(self, msg):
        """Convert calibrate PSU msg from GUI to CLI list for subprocess.

        Parameters
        ----------
        msg : dict
            Dictionary of settings sent from the GUI.

        Returns
        -------
        args : list
            List of command line arguments to parse to CLI.
        """
        # TODO: format run msg dict into args
        args = msg

        return args

    def _cal_psu(self, msg):
        """Measure the reference photodiode as a funtcion of LED current."""
        args = self._format_cal_psu_msg(msg)
        self._start_or_resume_subprocess(args)

    def _format_home_msg(self, msg):
        """Convert stage home msg from GUI to CLI list for subprocess.

        Parameters
        ----------
        msg : dict
            Dictionary of settings sent from the GUI.

        Returns
        -------
        args : list
            List of command line arguments to parse to CLI.
        """
        # TODO: format run msg dict into args
        args = msg

        return args

    def _home(self, msg):
        """Home the stage."""
        args = self._format_home_msg(msg)
        self._start_or_resume_subprocess(args)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mqtthost",
        default="127.0.0.1",
        help="IP address or hostname of MQTT broker.",
    )
    parser.add_argument(
        "--topic", default="gui/#", help="Topic for MQTT client to subscribe to.",
    )
    args = parser.parse_args()

    with CLIMQTT(args.mqtthost, args.topic) as mqttc:
        mqttc.loop_forever()
