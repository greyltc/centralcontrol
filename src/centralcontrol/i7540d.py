"""ICPCON I-7540D CANBUS-ethernet gateway control library.

The full instrument manual, including the programming guide, can be found at
https://www.icpdas.com/en/download/index.php?model=I-7540D-G.
"""

import socket
from typing import List, Dict
import warnings


class I7540d:
    """ICPCON I-7540D CANBUS-ethernet gateway."""

    DEVICE_PORT = 10000
    CAN_PORT = 10003
    CAN_FRAME_TERMCHAR = "\r"

    CAN_ERROR_CODES = {
        1: "The head character of the command string is invalid.",
        2: "The length of the command string is invalid.",
        3: "The value of CAN identifier is invalid.",
        4: "The value of CAN data length is invalid.",
        5: "Reserved",
    }

    CAN_BAUD_RATES = {
        0: "10k",
        1: "20k",
        2: "50k",
        3: "100k",
        4: "125k",
        5: "250k",
        6: "500k",
        7: "800k",
        8: "1000k",
        9: "User defined",
    }

    CAN_SPECIFICATIONS = {0: "2.0A", 1: "2.0B"}

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context related to this object.

        Make sure everything gets cleaned up properly.
        """
        self.disconnect()

    def __init__(self, host: str = "192.168.255.1", timeout: float = 5):
        """Construct object.

        Parameters
        ----------
        host : str
            IP address or hostname of instrument.
        timeout : int
            Socket timeout in s.
        """
        self._host = host
        self._timeout = timeout

        self._dev_client = None
        self._can_client = None

    @property
    def host(self) -> str:
        """Get the hostname."""
        return self._host

    @host.setter
    def host(self, host: str):
        """Set the hostname if not connected.

        Parameters
        ----------
        host : str
            IP address or hostname of instrument.
        """
        if self.connected:
            warnings.warn("Cannot change hostname while a connection is established.")
        else:
            self._host = host

    @property
    def timeout(self) -> float:
        """Get the timeout."""
        return self._timeout

    @timeout.setter
    def timeout(self, timeout: float):
        """Set the timeout.

        Parameters
        ----------
        timeout : int
            Socket timeout in s.
        """
        self._timeout = timeout
        if self._dev_client is not None and self._can_client is not None:
            self._dev_client.settimeout(self._timeout)
            self._can_client.settimeout(self._timeout)

    @property
    def connected(self):
        """Get connection status.

        This property only reports whether a flag that changes when the connect and
        disconnect methods are called. It doesn't actually test the socket. It's
        possible that this property can return True but the connection has been closed
        by the server.
        """
        return self._dev_client is not None and self._can_client is not None

    def connect(self) -> None:
        """Open client connections to the instrument's device and CAN ports."""
        if self._dev_client is None and self._can_client is None:
            self._dev_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._can_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            self._dev_client.settimeout(self._timeout)
            self._dev_client.connect((self.host, self.DEVICE_PORT))

            self._can_client.settimeout(self._timeout)
            self._can_client.connect((self.host, self.CAN_PORT))
        else:
            warnings.warn("A connection has already been established so cannot create a new one.")

    def disconnect(self) -> None:
        """Disconnect client connections."""
        if self._dev_client is not None and self._can_client is not None:
            self._dev_client.close()
            self._can_client.close()
        else:
            warnings.warn("No connection has been established so nothing to disconnect.")

        self._dev_client = None
        self._can_client = None

    # def _connect_and_send(self, client: socket.socket, cmd: str, port: int) -> None:
    #     """Connect to client and send a command.

    #     Parameters
    #     ----------
    #     client : socket.socket
    #         Socket client object.
    #     cmd : str
    #         Command string.
    #     port : int
    #         Port to send command to.
    #     """
    #     client.settimeout(self.timeout)
    #     client.connect((self.host, port))
    #     client.sendall(cmd.encode("ascii"))

    def _send(self, client: socket.socket, cmd: str) -> None:
        """Send a command.

        Parameters
        ----------
        client : socket.socket
            Socket client object.
        cmd : str
            Command string.
        """
        client.sendall(cmd.encode("ascii"))

    def _recv(self, client: socket.socket, length: int = 32) -> str:
        """Receive a response.

        Parameters
        ----------
        client : socket.socket
            Socket client object.
        length : int
            Number of bytes to read.

        Returns
        -------
        resp : str
            Response string.
        """
        return client.recv(length).decode("ascii")

    # def _device_read(self, cmd: str) -> str:
    #     """Read a device setting.

    #     All device read commands are prepended with "99" automatically by this
    #     function, as required by the device.

    #     Parameters
    #     ----------
    #     cmd : str
    #         Message string indicating what to read.

    #     Returns
    #     -------
    #     resp : str
    #         Response string.
    #     """
    #     with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
    #         self._connect_and_send(client, f"99{cmd}", self.DEVICE_PORT)
    #         resp = self._recv(client)

    #     return resp

    def _device_read(self, cmd: str) -> str:
        """Read a device setting.

        All device read commands are prepended with "99" automatically by this
        function, as required by the device.

        Parameters
        ----------
        cmd : str
            Message string indicating what to read.

        Returns
        -------
        resp : str
            Response string.
        """
        self._send(self._dev_client, f"99{cmd}")
        return self._recv(self._dev_client)

    # def _device_write(self, cmd: str):
    #     """Write a device setting.

    #     All device write commands are prepended with "99" automatically by this
    #     function, as required by the device.

    #     Parameters
    #     ----------
    #     cmd : str
    #         Message string indicating what to write.

    #     Returns
    #     -------
    #     resp : str
    #         Response string.
    #     """
    #     with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
    #         self._connect_and_send(client, f"99{cmd}", self.DEVICE_PORT)

    def _device_write(self, cmd: str):
        """Write a device setting.

        All device write commands are prepended with "99" automatically by this
        function, as required by the device.

        Parameters
        ----------
        cmd : str
            Message string indicating what to write.

        Returns
        -------
        resp : str
            Response string.
        """
        self._send(self._dev_client, f"99{cmd}")

    def get_status(self) -> Dict:
        """Read the status value of the gateway.

        See manual for bit settings in registers.

        Returns
        -------
        status : dict
            Dictionary of various status indicators.
        """
        raw_status = self._device_read("S")
        if raw_status[0] != "!":
            raise ValueError(f"Invalid command. Response: {raw_status}.")

        can_baud = self.CAN_BAUD_RATES[int(raw_status[1], 16)]
        can_reg = f"{int(raw_status[2:4], 16):08b}"
        can_tx_err_n = int(raw_status[4:6], 16)
        can_rx_err_n = int(raw_status[6:8], 16)
        can_fifo_overflow_flag = f"{int(raw_status[8], 16):04b}"

        return {
            "can_baud": can_baud,
            "can_register": can_reg,
            "can_tx_err_n": can_tx_err_n,
            "can_rx_err_n": can_rx_err_n,
            "can_fifo_overflow_flag": can_fifo_overflow_flag,
        }

    def clear_error(self, reinitialize: bool = False):
        """Clear the CAN error flag and FIFO on the module.

        Parameters
        ----------
        reinitialize : bool
            Choose whether to reinitialize the CAN hardware chip of the module.
        """
        self._device_write("CRA" if reinitialize else "C")

    def reboot(self):
        """Reboot the gateway."""
        self._device_write("RA")

    def get_can_config(self) -> Dict:
        """Read the CAN configuration.

        Returns
        -------
        can_config_resp : dict
            CAN configuration dictionary.
        """
        raw_can_config = self._device_read("#P1")
        if raw_can_config[:2] != "14":
            raise ValueError(f"Invalid command. Response: {raw_can_config}.")

        can_specification = int(raw_can_config[2], 16)
        can_baud = int(raw_can_config[3], 16)
        can_acc_code_reg = raw_can_config[4:12]
        can_acc_mask_reg = raw_can_config[12:20]
        can_err_resp = bool(int(raw_can_config[20]))
        can_timestamp_resp = bool(int(raw_can_config[21]))

        return {
            "can_specification": can_specification,
            "can_baud": can_baud,
            "can_acc_code_reg": can_acc_code_reg,
            "can_acc_mask_reg": can_acc_mask_reg,
            "can_err_resp": can_err_resp,
            "can_timestamp_resp": can_timestamp_resp,
        }

    def set_can_config(self, can_config: Dict):
        """Change the CAN configuration.

        Returns
        -------
        can_config : dict
            CAN configuration dictionary. The dictionary values have the following
            types:
                can_specification: int (see manual and CAN_SPECIFICATIONS class
                    variable for meanings of integer values)
                can_baud: int (see table in manual and CAN_BAUD_RATES class variable
                    for meanings of integer values)
                can_acc_code_reg: str (see manual for string format)
                can_acc_mask_reg: str (see manual for string format)
                can_err_resp: bool (False = 0 = Disabled, True = 1 = Enabled)
                can_timestamp_resp: bool (False = 0 = Disabled, True = 1 = Enabled)
        """
        can_specification: int = can_config["can_specification"]
        can_baud: int = can_config["can_baud"]
        can_acc_code_reg: str = can_config["can_acc_code_reg"]
        can_acc_mask_reg: str = can_config["can_acc_mask_reg"]
        can_err_resp: bool = can_config["can_err_resp"]
        can_timestamp_resp: bool = can_config["can_timestamp_resp"]

        cmd = f"$P114{can_specification}{can_baud}{can_acc_code_reg}{can_acc_mask_reg}" + f"{int(can_err_resp)}{int(can_timestamp_resp)}"
        raw_can_config_resp = self._device_read(cmd)
        if raw_can_config_resp != "OK":
            raise ValueError(f"Invalid command. Response: {raw_can_config_resp}.")

    def _format_standard_can_command(self, identifier: int, data: List[int]) -> str:
        """Convert a standard identifier and data into the gateway format to send.

        Parameters
        ----------
        identifier : int
            11-bit (000-7FF in hexadecimal or 0-2047 in decimal) message identifier.
        data : list of int
            Data to be sent. Must only contain integers that can be represented in
            8-bits. A maximum of 8 data bytes is currently supported.

        Returns
        -------
        cmd : str
            Formatted command.
        """
        # check if identifier is valid for a standard dataframe
        if (identifier > 0x7FF) or (identifier < 0):
            raise ValueError(f"Invalid identifier: {identifier}. Must in range 0x000-0x7FF (0-2047).")

        # TODO: handle longer dataframes
        # check if length of data can fit in a single dataframe
        dlc = len(data)
        if dlc > 8:
            raise ValueError(f"Input data frame is too long. It contains {dlc} elements but can " + "only be a maximum of 8 for a standard CAN dataframe.")

        # check if all elements of the data list are compatible with 8-bit ints
        if any(i > 255 for i in data):
            raise ValueError("An element in the data list is greater than 255 so is not a valid " + f"8-bit code: {data}. ICPCON I-7540D can only handle valid 8-bit " + "codes.")

        data_str = "".join([f"{i:02x}" for i in data])

        return f"t{identifier:03x}{dlc}{data_str}{self.CAN_FRAME_TERMCHAR}"

    def _format_standard_can_response(self, response: str) -> Dict:
        """Convert a received standard dataframe into a response dictionary.

        The gateway formats responses as a string of hexidecimal numbers.

        Parameters
        ----------
        response : str
            Standard dataframe received by the gateway.

        Returns
        -------
        resp_dict : dict
            Formatted response as a dictionary with keys: `identifier` and `data`.
            `identifier` is an int and `data` is a list of up to 8 `int`'s.
        """
        # check for command error
        if response[0] == "?":
            raise ValueError(self.CAN_ERROR_CODES[int(response[1:-1], 16)])

        # the gateway formats received standard dataframes with a "t" as the
        # first character, which can be ignored
        resp_id = int(response[1:4], 16)

        # read number of bytes in response
        resp_dlc = int(response[4], 16)

        # read double number of bytes in data part of response because response
        # is a string representing bytes, not bytes directly
        resp_data = response[5 : 5 + 2 * resp_dlc]

        # convert hex formatted data string to list of ints
        resp_data = [int(resp_data[i : i + 2], 16) for i in range(0, len(resp_data), 2)]

        return {"identifier": resp_id, "data": resp_data}

    # def standard_dataframe_query(self, identifier: int, data: List[int]) -> Dict:
    #     """Send a standard format CAN dataframe and receive a response.

    #     Parameters
    #     ----------
    #     identifier : int
    #         11-bit (000-7FF in hexadecimal or 0-2047 in decimal) message identifier.
    #     data : list of int
    #         Data to be sent. Must only contain integers that can be represented in
    #         8-bits. A maximum of 8 data bytes is currently supported.

    #     Returns
    #     -------
    #     resp_dict : dict
    #         Formatted response as a dictionary with keys: `identifier` and `data`.
    #         `identifier` is an int and `data` is a list of up to 8 `int`'s.
    #     """
    #     cmd = self._format_standard_can_command(identifier, data)
    #     with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
    #         self._connect_and_send(client, cmd, self.CAN_PORT)

    #         # the gateway formats received standard dataframes as strings with a
    #         # maximum length of 22, including \r termchar
    #         resp = self._recv(client, 22)

    #     return self._format_standard_can_response(resp)

    def standard_dataframe_query(self, identifier: int, data: List[int]) -> Dict:
        """Send a standard format CAN dataframe and receive a response.

        Parameters
        ----------
        identifier : int
            11-bit (000-7FF in hexadecimal or 0-2047 in decimal) message identifier.
        data : list of int
            Data to be sent. Must only contain integers that can be represented in
            8-bits. A maximum of 8 data bytes is currently supported.

        Returns
        -------
        resp_dict : dict
            Formatted response as a dictionary with keys: `identifier` and `data`.
            `identifier` is an int and `data` is a list of up to 8 `int`'s.
        """
        cmd = self._format_standard_can_command(identifier, data)
        self._send(self._can_client, cmd)

        # the gateway formats received standard dataframes as strings with a
        # maximum length of 22, including \r termchar
        resp = self._recv(self._can_client, 22)

        return self._format_standard_can_response(resp)

    # def standard_dataframe_write(self, identifier: int, data: List[int]) -> None:
    #     """Send a standard format CAN dataframe.

    #     Parameters
    #     ----------
    #     identifier : int
    #         11-bit (000-7FF in hexadecimal or 0-2047 in decimal) message identifier.
    #     data : list of int
    #         Data to be sent. Must only contain integers that can be represented in
    #         8-bits. A maximum of 8 data bytes is currently supported.
    #     """
    #     cmd = self._format_standard_can_command(identifier, data)
    #     with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
    #         self._connect_and_send(client, cmd, self.CAN_PORT)

    def standard_dataframe_write(self, identifier: int, data: List[int]) -> None:
        """Send a standard format CAN dataframe.

        Parameters
        ----------
        identifier : int
            11-bit (000-7FF in hexadecimal or 0-2047 in decimal) message identifier.
        data : list of int
            Data to be sent. Must only contain integers that can be represented in
            8-bits. A maximum of 8 data bytes is currently supported.
        """
        cmd = self._format_standard_can_command(identifier, data)
        self._send(self._can_client, cmd)
