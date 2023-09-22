"""Ark Metrica MUX-481-CAN control library."""

from typing import List, Dict
import warnings

import centralcontrol.i7540d as i7540d
from centralcontrol.logstuff import get_logger


class Mux481can:
    """Ark Metrica MUX-481-CAN multiplexor."""

    enabled = False

    ERROR_CODES = {
        0: "General error",
        1: "Invalid command",
        2: "Invalid pin number",
        3: "Invalid bitmask",
    }

    MAX_RETRIES = 3

    SLOT_NUMBERS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context related to this object.

        Make sure everything gets cleaned up properly.
        """
        self.disconnect()

    def __init__(self, address: str = "192.168.255.1", timeout: float = 2):
        """Construct object.

        Parameters
        ----------
        address : str
            IP address or hostname of gateway.
        timeout : int
            Socket timeout in s.
        expected_muxes: list[str] = [], enabled=True
        """

        # setup logging
        self.lg = get_logger(".".join([__name__, type(self).__name__]))

        self.lg.debug(f"Initialized with {address=}")

        self._address = address
        self._timeout = timeout
        self.gateway = i7540d.I7540d(address, timeout)

    @property
    def address(self):
        """Read the gateway address."""
        return self._address

    @property
    def timeout(self):
        """Read the gateway timeout."""
        return self._timeout

    def connect(self) -> None:
        """Open client connections to the gateway."""
        self.gateway.connect()
        self.lg.debug(f"Gateway connect called")

        # check for existing transmit/receive errors and reboot to clear if necessary
        status = self.gateway.get_status()
        self.lg.debug(f"Gateway status: {status=}")
        if status["can_tx_err_n"] != 0 or status["can_rx_err_n"] != 0:
            warnings.warn("Gateway connected with transmit/receive errors flagged. Rebooting to " + "clear...")

            # gateway is in an error state so reboot to clear the error
            self.gateway.reboot()

            # after rebooting the socket connection needs to be cycled
            self.gateway.disconnect()
            for attempt in range(3):
                try:
                    self.gateway.connect()
                    break
                except TimeoutError as err:
                    self.gateway.disconnect()
                    if attempt == 2:
                        raise err

        for board_address in range(1, 17):
            idn = self.get_board_idn(board_address)
            self.lg.debug(f"Board {board_address} idn: {idn}")

    def disconnect(self) -> None:
        """Disconnect from gateway."""
        self.gateway.disconnect()

    def _query(self, board_addr: int, data: List[int]) -> Dict:
        # sourcery skip: move-assign-in-block, use-fstring-for-concatenation
        """Query the gateway and check for errors.

        Parameters
        ----------
        board_addr : int
            Address of the board in the mux array.
        data : list of int
            Data portion of CAN bus frame.

        Parameters
        ----------
        resp : dict
            Response dictionary.
        """
        # send query followed by a check for rx and tx errors
        # retry query if there are any errors
        rx_tx_err = True
        resp = {}
        for _ in range(self.MAX_RETRIES):
            # query data
            resp = self.gateway.standard_dataframe_query(board_addr, data)

            # check for rx and tx errors
            status = self.gateway.get_status()
            if (status["can_tx_err_n"] == 0) and (status["can_rx_err_n"] == 0):
                # there were no errors
                rx_tx_err = False
                break
            else:
                # there was an error so reboot the gateway to clear it
                self.gateway.reboot()

                # after rebooting the socket connection needs to be cycled
                self.gateway.disconnect()

                # establishing a new connection may timeout during the device boot
                # process so re-attempt connection if this happens
                for attempt in range(3):
                    try:
                        self.gateway.connect()
                        break
                    except TimeoutError as err:
                        self.gateway.disconnect()
                        if attempt == 2:
                            raise err

            warnings.warn(f"Re-attempting query. Board address: {board_addr}, data: {data}.")

        if rx_tx_err:
            raise RuntimeError("CANBUS gateway transmit/receive errors encountered during " + f"{self.MAX_RETRIES} query attempts.")

        # check for mux response errors
        self._error_check(resp)

        return resp

    def _error_check(self, resp: Dict) -> None:
        """Check a response for errors.

        Parameters
        ----------
        resp : dict
            Response dictionary.
        """
        data = resp["data"]
        if chr(data[0]) == "e":
            raise ValueError(f"MUX ERROR: {self.ERROR_CODES[data[1]]}")

    def _data_to_str(self, data: List[int]) -> str:
        """Convert list of ints to string.

        Parameters
        ----------
        data : list of int
            Data portion of CAN bus frame.

        Returns
        -------
        resp : str
            Data formatted into an ASCII string.
        """
        return "".join([chr(i) for i in data])

    def get_board_idn(self, board_addr: int) -> str:
        """Get identity string of a mux board.

        Parameters
        ----------
        board_addr : int
            Address of the board in the mux array.

        Returns
        -------
        idn : str
            Identity string of the board.
        """
        # query raw parts of id string
        ser_f = self._query(board_addr, [ord("i")])
        fw_ver_f = self._query(board_addr, [ord("v")])
        manuf_f = self._query(board_addr, [ord("m")])
        model_f = self._query(board_addr, [ord("d")])

        # format parts of id string from response frames
        ser = "".join([f"{i:02x}" for i in ser_f["data"]])
        fw_ver = self._data_to_str(fw_ver_f["data"])
        manuf = self._data_to_str(manuf_f["data"])
        model = self._data_to_str(model_f["data"])

        return f"{manuf},{model},{ser},{fw_ver}"

    def pin_on(self, baord_addr: int, pin: int) -> None:
        """Turn on a mux pin.

        Parameters
        ----------
        board_addr : int
            Address of the board in the mux array.
        pin : int
            Pin number on mux board to change state, 0-indexed.
        """
        if (pin < 0) or (pin > 31):
            raise ValueError(f"Invalid pin number: {pin}. Pin numbers must be in the range 0-31.")
        self._query(baord_addr, [ord("n"), pin])

    def pin_off(self, baord_addr: int, pin: int) -> None:
        """Turn off a mux pin.

        Parameters
        ----------
        board_addr : int
            Address of the board in the mux array.
        pin : int
            Pin number on mux board to change state, 0-indexed.
        """
        if (pin < 0) or (pin > 31):
            raise ValueError(f"Invalid pin number: {pin}. Pin numbers must be in the range 0-31.")
        self._query(baord_addr, [ord("f"), pin])

    def set_pins(self, board_addr: int, pins: List[int] | None = None) -> None:
        # sourcery skip: move-assign-in-block, use-fstring-for-concatenation
        """Turn on all pins given in a list and turn off all others.

        Parameters
        ----------
        board_addr : int
            Address of the board in the mux array.
        pins : int
            Pin numbers on mux board to change state, 0-indexed.
        """
        bank1 = 0
        bank2 = 0
        bank3 = 0
        bank4 = 0
        if (pins != []) and (pins is not None):
            for pin in pins:
                if (pin >= 0) and (pin < 8):
                    bank1 += 1 << pin
                elif (pin >= 8) and (pin < 16):
                    bank2 += 1 << (pin % 8)
                elif (pin >= 16) and (pin < 24):
                    bank3 += 1 << (pin % 8)
                elif (pin >= 24) and (pin < 32):
                    bank4 += 1 << (pin % 8)
                else:
                    raise ValueError(f"Invalid pin number: {pin}. Pin numbers must be in the range " + "0-31.")

        self._query(board_addr, [ord("s"), bank4, bank3, bank2, bank1])

    def get_pins(self, board_addr: int) -> List[int]:
        """Get a list of pins that are turned on.

        Parameters
        ----------
        board_addr : int
            Address of the board in the mux array.

        Returns
        -------
        pins : int
            Pin numbers on mux board that are turned on, 0-indexed.
        """
        resp = self._query(board_addr, [ord("g")])

        # get value of each relay bank and reverse list to start from 0th bank
        bank_values = resp["data"]
        bank_values.reverse()

        # get list of pins that are turned on
        pins = []
        for bank, value in enumerate(bank_values):
            # convert bank value to binary string and reverse it to start from 0th pin
            value_str = f"{value:08b}"[::-1]

            # count along reversed string picking out the indices where the string is 1
            # those are the pins that are on
            pins.extend(bank * 8 + pin for pin in range(8) if value_str[pin] == "1")

        return pins

    def slot_to_addr(self, slot: str) -> int:
        """Convert a slot label to a board address.

        Slot labels are capital letters that ascend alphabetically from 'A'.

        Parameters
        ----------
        slot : str
            Slot label.

        Returns
        -------
        board_addr : int
            Mux board address.
        """
        board_addr = ord(slot) - ord("A") + 1
        if board_addr not in self.SLOT_NUMBERS:
            raise ValueError(f"Invalid slot: {slot}")

        return board_addr

    def set_mux(self, mux_settings: list[tuple[str, int]] | list[tuple[str, str]]) -> None:
        """program mux with failure recovery logic. returns nothing but raises a value error on failure"""
        if self.enabled:
            if (mux_settings is None) or (mux_settings == []):
                mux_settings = [("OFF", 0)]

            for pixel in mux_settings:
                slot, device = pixel
                if slot.startswith("EXT") or (slot == "OFF"):
                    # turn all mux pins off on all boards
                    for _board_addr in self.SLOT_NUMBERS:
                        self.set_pins(_board_addr, None)
                    break
                else:
                    board_addr = self.slot_to_addr(slot)

                    if device == 0:
                        self.lg.debug(f"Pixel switched with int: {pixel=}")
                        # turn off all pins on board
                        self.set_pins(board_addr, None)
                    elif isinstance(device, int):
                        self.lg.debug(f"Pixel switched with int: {pixel=}")
                        # list all common pins in the slot, which should turn on
                        # also add device pins that should also turn on
                        # pins are 0-indexed by the mux firmware
                        pins = [
                            device - 1,
                            device - 1 + 8,
                            16,
                            17,
                            18,
                            19,
                            24,
                            25,
                            26,
                            27,
                        ]

                        # turn on only requested pins, turning off all others
                        self.set_pins(board_addr, pins)
                    elif isinstance(device, str):
                        self.lg.debug(f"Pixel switched with str: {pixel=}")
                        the_bin = format(int(device), "#018b")[2::]

                        common_bitmask = the_bin[-8:]
                        pins = []
                        if common_bitmask[0] == "1":
                            # turn on TOP common pins (nearest to devices 5&6)
                            pins.extend([17, 19, 25, 27])

                        if common_bitmask[1] == "1":
                            # turn on BOT common pins (nearest to devices 1&2)
                            pins.extend([16, 18, 24, 26])

                        # get device selection from bitmask
                        device_bitmask = the_bin[0:8]
                        device_bitmask = device_bitmask[::-1]
                        for bit_ix, bit in enumerate(device_bitmask):
                            if bit == "1":
                                pins.extend([bit_ix, bit_ix + 8])

                        self.set_pins(board_addr, pins)
                    else:
                        raise ValueError(f"Invalid device designator: {device}, of type: " + f"{type(device)}.")
