"""Client for running the CLI based on MQTT messages."""

import json
import os
import signal
import subprocess
import warnings

import paho.mqtt.client as mqtt
import psutil

import central_control.cli


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

        # instantiate cli object to read attributes
        self.cli = central_control.cli.cli()

        # check if graceful keyboard interrupt is available on running os
        if (self.os_name := os.name) != "posix":
            warnings.warn("The CLI cannot be stopped gracefully on this OS.")

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context related to this object.

        Make sure everything gets cleaned up properly.
        """
        try:
            self._stop()
        except psutil.NoSuchProcess:
            # subprocess has already stopped
            pass
        self.loop_stop()
        self.disconnect()

    def on_message(self, mqttc, obj, msg):
        """Act on an MQTT message."""
        m = json.loads(msg.payload)

        # perform action depending on which button generated the message
        if (subtopic := msg.topic.split("/")[-1]) == "config":
            self._save_config(m)
        elif subtopic == "run":
            self._run(m)
        elif subtopic == "pause":
            self._pause()
        elif subtopic == "stop":
            self._stop()
        elif subtopic == "cal_eqe":
            self._cal_eqe(m)
        elif subtopic == "cal_psu":
            self._cal_psu(m)
        elif subtopic == "home":
            self._home()
        elif subtopic == "goto":
            self._goto(m)
        elif subtopic == "read_stage":
            self._read_stage()

    def _save_config(self, msg):
        """Save config string to cached file so CLI can use it.

        Parameters
        ----------
        msg : str
            Configuration file as a string.
        """
        self.config_file_path = self.cli.cache.joinpath(self.cli.config_file)
        with open(self.config_file_path, "w") as f:
            f.wrtie(msg)

    def _start_or_resume_subprocess(self, args, subtopic=""):
        """Start or resume a subprocess.

        Start a new subprocess is there isn't one running or resume a subprocess if one
        is paused and run has been pressed.

        Parameters
        ----------
        args : list
            List of command line arguments to parse to CLI.
        subtopic : str
            Message subtopic. This is used to determine whether a paused process should
            be resumed.
        """
        # start process if there is none
        if self.proc is None:
            self._start_subprocess(args)
        else:
            try:
                # try to resume the process if run pressed and it's paused
                if (self.proc.status() == "stopped") & (subtopic == "run"):
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
        self._start_or_resume_subprocess(args, subtopic="run")

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
                if self.os_name == "posix":
                    # posix systems have a keyboard interrupt signal that allows the
                    # subprocess to be cleaned up gracefully if the running function
                    # is in a context manager.
                    self.proc.send_signal(signal.SIGINT)
                else:
                    # no graceful way to interrupt a subprocess on windows so this
                    # kills it without cleanup.
                    # not sure what happens on other os's so this method is probably
                    # least likely to have undesirable side-effects.
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

    def _home(self):
        """Home the stage."""
        args = ["--home"]
        self._start_or_resume_subprocess(args)

    def _goto(self, msg):
        """Go to a stage position."""
        args = ["--goto"] + msg
        self._start_or_resume_subprocess(args)

    def _read_stage(self):
        """Read the stage position."""
        args = ["--read-stage"]
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
