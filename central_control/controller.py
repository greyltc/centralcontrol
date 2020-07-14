"""Multiplexor and stage controller."""

from telnetlib import Telnet

# the firmware's prompt
PROMPT = b">>> "

# client-->server transmission line ending the firmware expects
EOL = b"\r\n"


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

    def __init__(self, address="10.46.0.233", read_timeout=1):
        """Contrust object.

        Parameters
        ----------
        address : str
            Resource address.
        read_timeout : float
            Read timeout in seconds.
        """
        self.address = address
        self.read_timeout = read_timeout

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

    def home(self, axis):
        """Send a home command to the stage.

        Parameters
        ----------
        axis : {1,2,3}
            Stage axis 1, 2, or 3 (x, y, and z).

        Returns
        -------
        response : str
            Response to home command.
        """
        self.tn.send_cmd(f"h{axis}")

        return self.tn.read_response(timeout=self.read_timeout)

    def get_length(self, axis):
        """Query the stage length along an axis.

        Parameters
        ----------
        axis : {1,2,3}
            Stage axis 1, 2, or 3 (x, y, and z).

        Returns
        -------
        length : int
            Length of stage in steps.
        """
        self.tn.send_cmd(f"l{axis}")

        return int(self.tn.read_response(timeout=self.read_timeout))

    def get_position(self, axis):
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

        return int(self.tn.read_response(timeout=self.read_timeout))

    def goto(self, axis, position):
        """Go to stage position in steps.

        Parameters
        ----------
        axis : {1,2,3}
            Stage axis 1, 2, or 3 (x, y, and z).
        position : int
            Number of steps along stage to move to.

        Returns
        -------
        response : str
            Response to goto command.
        """
        self.tn.send_cmd(f"g{axis}{position:.0f}")

        return self.tn.read_response(timeout=self.read_timeout)

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

        Return
        ------
        response : str
            Response to select mux relay command.
        """
        # break all connections
        self.clear_mux()

        # convert column number to a letter
        letters = "abcdefghijklmnopqrstuvwxyz"
        col = letters[col - 1]

        # connect relay
        self.tn.send_cmd(f"s{row}{col}{pixel}")

        return self.tn.read_response(timeout=self.read_timeout)

    def clear_mux(self):
        """Open all multiplexor relays.

        Returns
        -------
        response : str
            Response to deselect all mux relays command.
        """
        self.tn.send_cmd("s")

        return self.tn.read_response(timeout=self.read_timeout)

    def get_port_expanders(self):
        """Check which port expanders are available.

        Returns
        -------
        expander_bitmask : str
            Decimal string representing bitwise connected state of port expanders.
        """
        self.tn.send_cmd("c")

        return self.tn.read_response(timeout=self.read_timeout)

    def set_relay(self, exp):
        """Choose EQE or IV connection.

        Parameters
        ----------
        exp : {"eqe", "iv"}
            Experiment name: either "eqe" or "iv".
        """
        self.tn.send_cmd(exp)

        return self.tn.read_response(timeout=self.read_timeout)
