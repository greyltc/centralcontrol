"""Multiplexor and stage controller."""

from telnetlib import Telnet
import time

# the firmware's prompt
PROMPT = b">>> "

# client-->server transmission line ending the firmware expects
EOL = b"\r\n"

default_host = "10.46.0.233"


class MyTelnet(Telnet):
    """Telnet client with read/write formatting."""

    def read_response(self, timeout=None):
        """Read controller response.

        Parameters
        ----------
        timeout : float
            Read timeout in seconds.
        """
        self.empty_response = False
        resp = self.read_until(PROMPT, timeout=timeout)
        ret = resp.rstrip(PROMPT).decode().strip()
        if len(resp) == 0:
            self.empty_response = True
        return ret

    def send_cmd(self, cmd):
        """Send a command to the controller.

        Parameters
        ----------
        cmd : str
            Controller command.
        """
        return self.write(cmd.encode() + EOL)


class controller:
    """Mux and stage controller."""

    def __init__(self, address=default_host):
        """Contrust object."""
        self.address = address

    def connect(self):
        """Connect to the controller using Telnet."""
        self.tn = MyTelnet(self.address)

        # get the welcome message
        welcome_message = self.tn.read_response(timeout=2)
        print(f"{welcome_message}")

        # query the version
        self.tn.send_cmd("v")
        self.version_message = self.tn.read_response(timeout=2)
        print(f"Got version request response: {self.version_message}")

    def home(self, axis, timeout=80, length_poll_sleep=0.1):
        """Home the stage.

        Parameters
        ----------
        axis : {1,2,3}
            Stage axis 1, 2, or 3 (x, y, and z).
        timeout : float
            Timeout in seconds. Raise an error if it takes longer than expected to home
            the stage.
        length_poll_sleep : float
            Time to wait in s before polling the current length of the stage to
            determine whether homing has finished.

        Returns
        -------
        ret_val : int
            The length of the stage along the given axis in steps for a successful
            home. If there was a problem an error code is returned:

                * -1 : Timeout error.
                * -2 : Programming error.
        """
        ret_val = -2
        print("HOMING!")
        self.tn.send_cmd(f"h{axis}")
        response = self.tn.read_response(timeout)

        if response != "":
            raise (ValueError(f"Homing the stage failed: {response}"))
        else:
            if self.tn.empty_response is True:
                # we never got the prompt back
                ret_val = -2
            else:
                t0 = time.time()
                dt = 0
                while dt < timeout:
                    time.sleep(length_poll_sleep)
                    self.tn.send_cmd(f"l{axis}")
                    ret_val = int(self.tn.read_response())
                    if ret_val > 0:
                        # axis length has been returned
                        break
                    dt = time.time() - t0

        return ret_val

    def read_pos(self, axis):
        """Read the current stage position along a given axis.

        Parameters
        ----------
        axis : int
            Axis to read, 1-indexed.

        Returns
        -------
        steps : int
            Stage position in steps.
        """
        self.tn.send_cmd(f"r{axis}")

        return int(self.tn.read_response(timeout=1))

    def goto(self, axis, position, timeout=20, retries=5, position_poll_sleep=0.5):
        """Go to stage position in steps.

        Uses polling to determine when stage motion is complete.

        Parameters
        ----------
        axis : {1,2,3}
            Stage axis 1, 2, or 3 (x, y, and z).
        position : int
            Number of steps along stage to move to.
        timeout : float
            Timeout in seconds. Raise an error if it takes longer than expected to
            reach the required position.
        retries : int
            Number of attempts to send command before failing. The command will be sent
            this many times within the timeout period.
        position_poll_sleep : float
            Time to wait in s before polling the current position of the stage to
            determine whether the required position has been reached.

        Returns
        -------
        ret_val : int
            Return value:

                * 0 : Reached position successfully.
                * -1 : Command not accepted. Stage probably isn't homed / has stalled.
                * -2 : Max retries / timeout exceeded.
                * -3 : Programming error.
        """
        # must be a whole number of steps
        position = round(position)
        attempt_timeout = timeout / retries

        while retries > 0:
            self.tn.send_cmd(f"g{axis}{position:.0f}")
            resp = self.tn.read_response(timeout=1)
            if resp == "":
                # goto command accepted
                loc = None
                t0 = time.time()
                now = 0
                # periodically poll for position
                while (loc != position) and (now <= attempt_timeout):
                    # ask for current position
                    self.tn.send_cmd(f"r{axis}")
                    loc = int(self.tn.read_response(timeout=1))
                    # for debugging
                    print(f"Location = {loc}")
                    time.sleep(position_poll_sleep)
                    now = time.time() - t0
                # exited above loop because of microtimeout, retry
                if now > attempt_timeout:
                    ret_val = -2
                    retries = retries - 1
                else:
                    # we got there
                    ret_val = 0
                    break
            else:
                # goto command fail. this likely means the stage is unhomed either
                # because it stalled or it just powered on
                ret_val = -1
                break

        return ret_val

    def set_mux(self, row, col, pixel):
        """Close a multiplexor relay.

        Breaks all connections before making a new one.

        Parameter
        ---------
        row : int
            Row position of substrate, 1-indexed.
        col : int
            Column position of substrate, 1-indexed.
        pixel : int
            Pixel on substrate, 1-indexed.
        """
        # break all connections
        self.clear_mux()

        # convert column number to a letter
        letters = "abcdefghijklmnopqrstuvwxyz"
        col = letters[col]

        # connect relay
        self.tn.send_cmd(f"s{row}{col}{pixel}")
        self.tn.read_response(timeout=0.5)

    def clear_mux(self):
        """Open all multiplexor relays."""
        self.tn.send_cmd("s")
        self.tn.read_response(timeout=0.5)

    def get_port_expanders(self):
        """Check which port expanders are available.

        Returns
        -------
        expander_bitmask : str
            Decimal string representing bitwise connected state of port expanders.
        """
        self.tn.send_cmd("c")

        return self.tn.read_response(timeout=0.5)
