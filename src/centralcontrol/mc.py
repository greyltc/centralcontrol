#!/usr/bin/env python3

import os
import socket
import time
from telnetlib import Telnet

from centralcontrol.logstuff import get_logger


class MC(object):
    """Interface for talking to the Master of Ceremonies (aka the control box/pc board)"""

    mux = None  # for dedicated mux hardware

    write_terminator = "\r\n"
    # read_terminator = b'\r\n'
    prompt_string = ">>> "
    prompt = prompt_string.encode()
    # prompt = read_terminator + prompt_string.encode()
    comms_timeout = 5.0  # telnet blocking operations timeout
    response_timeout = 6.0  # give the PCB this long to send a long message
    telnet_host = "localhost"
    telnet_port = 23
    firmware_version = None
    detected_muxes: list[str] = []
    detected_axes: list[str] = []
    expected_muxes: list[str] = []
    welcome_message = None
    enabled = True  # if this is false, everything here is a noop
    snaith_mux_pixel_lookup: dict[int, str] = {}
    otter_mux_pixel_lookup: dict[int, str] = {}

    class MyTelnet(Telnet):
        def read_response(self, timeout=None):
            found_prompt = False
            resp = self.read_until(MC.prompt, timeout=timeout)
            if resp.endswith(MC.prompt):
                found_prompt = True
            ret = resp.rstrip(MC.prompt).decode().strip()
            if len(resp) == 0:
                ret = None  # nothing came back (likely a timeout)
            return ret, found_prompt

        def send_cmd(self, cmd):
            if not cmd.endswith(MC.write_terminator):
                self.write(cmd.encode())
            else:
                self.write(cmd.encode() + MC.write_terminator.encode())

    def __init__(self, address: None | str = None, timeout: float = comms_timeout, expected_muxes: list[str] = [], enabled=True):
        self.comms_timeout = timeout  # pcb has this many seconds to respond

        # setup logging
        self.lg = get_logger(".".join([__name__, type(self).__name__]))

        # mux command lookup tables for direct latch programming mode
        self.snaith_mux_pixel_lookup = {
            0: "0",
            1: f"{(1<<8)+(1<<1)+(1<<0):05}",
            2: f"{(1<<9)+(1<<1)+(1<<0):05}",
            3: f"{(1<<10)+(1<<1)+(1<<0):05}",
            4: f"{(1<<11)+(1<<1)+(1<<0):05}",
            5: f"{(1<<12)+(1<<1)+(1<<0):05}",
            6: f"{(1<<13)+(1<<1)+(1<<0):05}",
            7: f"{(1<<14)+(1<<1)+(1<<0):05}",
            8: f"{(1<<15)+(1<<1)+(1<<0):05}",
        }
        self.otter_mux_pixel_lookup = {
            0: "0",
            1: f"{(1<<6)+(1<<7)+(1<<0):03}",
            2: f"{(1<<6)+(1<<7)+(1<<2):03}",
            3: f"{(1<<6)+(1<<7)+(1<<4):03}",
            4: f"{(1<<6)+(1<<7)+(1<<1):03}",
            5: f"{(1<<6)+(1<<7)+(1<<3):03}",
            6: f"{(1<<6)+(1<<7)+(1<<5):03}",
        }

        if address is None:
            # passing a None address makes everything in this class a noop
            self.enabled = False
        else:
            self.enabled = enabled
            addr_split = address.split(":")
            if len(addr_split) == 1:
                self.telnet_host = addr_split[0]
            else:
                h, p = address.split(":")
                self.telnet_host = h
                self.telnet_port = int(p)
            self.expected_muxes = expected_muxes

        self.lg.debug("Initialized")

    def __enter__(self):
        if self.enabled:
            self.connect()
        return self

    # figures out what muxes are connected
    def probe_muxes(self):
        if self.enabled:
            try:
                mux_int = int(self.query("c"))  # type: ignore
            except Exception as e:
                self.lg.debug("Failed to probe muxes: {e}")
            else:
                mux_bin_str = f"{mux_int:b}"
                mux_bin_str_rev = mux_bin_str[::-1]
                self.detected_muxes = []
                start_char = "A"
                for i, b in enumerate(mux_bin_str_rev):
                    if b == "1":
                        self.detected_muxes.append(chr(ord(start_char) + i))

    # figures out what axes are connected
    def probe_axes(self):
        if self.enabled:
            try:
                axes_int = int(self.query("e"))  # type: ignore
            except Exception as e:
                self.lg.debug("Failed to probe axes: {e}")
            else:
                axes_bin_str = f"{axes_int:b}"
                axes_bin_str_rev = axes_bin_str[::-1]
                self.detected_axes = []
                start_char = "1"
                for i, b in enumerate(axes_bin_str_rev):
                    if b == "1":
                        self.detected_axes.append(chr(ord(start_char) + i))

    def __exit__(self, type, value, traceback):
        if self.enabled:
            self.disconnect()
        return False

    def connect(self):
        """connects to control PCB"""
        if self.enabled:
            connection_retries = 2
            for attempt in range(connection_retries):
                self.tn = self.MyTelnet(self.telnet_host, self.telnet_port, timeout=self.comms_timeout)
                self.sf = self.tn.get_socket().makefile("rwb", buffering=0)
                self.tn.get_socket().settimeout(self.comms_timeout)  # in seconds

                if "ix" in os.name:
                    MC.set_keepalive_linux(self.tn.get_socket())  # let's try to keep our connection alive!

                self.welcome_message, win = self.tn.read_response(timeout=self.response_timeout)

                if not win:
                    self.lg.debug("Firmware did not present command prompt on connection")
                else:
                    self.firmware_version = self.query("v")
                    self.probe_muxes()
                    self.probe_axes()
                    self.lg.debug(f"v={self.firmware_version}|m={self.detected_muxes}|s={self.detected_axes}")
                    if (self.expected_muxes != []) and (self.expected_muxes != self.detected_muxes):
                        self.lg.debug(f"Got unexpected mux presence. Wanted: '{self.expected_muxes}' but got '{self.detected_muxes}")
                    else:  # connection success
                        break

                self.disconnect(method="reset")
            else:
                raise ValueError(f"Failed to connect to control PCB at {self.telnet_host}:{self.telnet_port}")

    def disconnect(self, method="exit"):
        """disconnects from control PCB"""
        if self.enabled:
            try:
                self.write(method)
            except Exception:
                pass
            try:
                self.sf.close()
            except Exception:
                pass
            try:
                self.tn.close()
            except Exception:
                pass

            if method == "reset":
                # sleep for a sec to allow the hardware to complete reset
                time.sleep(1)

    def write(self, cmd):
        if self.enabled:
            if not cmd.endswith(self.write_terminator):
                cmd = cmd + self.write_terminator

            self.sf.write(cmd.encode())
            self.sf.flush()

    # query with no ack check
    def query_nocheck(self, query):
        if self.enabled:
            self.write(query)
            resp = self.tn.read_response(timeout=self.comms_timeout)
        else:
            resp = ("", True)
        return resp

    # query with better error handling and with ack check
    def query(self, query):
        answer = None
        if self.enabled:
            ack = False
            try:
                answer, ack = self.query_nocheck(query)
            except Exception:
                self.lg.warning(f"Firmware comms failure while trying to send '{query}'")
            if ack == False:
                self.lg.warning(f"Firmware did not acknowledge '{query}'")
        return answer

    def expect_empty(self, cmd, tries=1):
        """sends a command that we expect an empty response to. retrurns success bool"""
        success = False
        rslt = None
        if self.enabled:
            for attempt in range(tries):
                rslt = None
                try:
                    rslt = self.query(cmd)
                    if rslt == "":
                        success = True
                        break
                except Exception as e:
                    pass
                self.lg.debug(f"Unexpected non-empty response from PCB during attempt #{attempt}: {cmd=} --> {rslt=}")
            else:
                self.lg.warning(f"Failed to get empty response from PCB: {cmd=} --> {rslt=} (after {tries} attempts)")
        else:
            success = True
        return success

    def expect_int(self, cmd, tries=1):
        """sends a command that we expect an intiger response to returns int or None"""
        rslt = None
        pcb_ans = None
        if self.enabled:
            for attempt in range(tries):
                pcb_ans = None
                try:
                    pcb_ans = self.query(cmd)
                    assert pcb_ans is not None, f"{pcb_ans is not None=}"
                    rslt = int(pcb_ans)
                    break
                except Exception as e:
                    pass
                self.lg.debug(f"Unexpected non-int response from PCB during attempt #{attempt}: {cmd=} --> {pcb_ans=}")
            else:
                self.lg.warning(f"Failed to get intiger response from PCB: {cmd=} --> {pcb_ans=} (after {tries} attempts)")
        return rslt

    def set_mux(self, mux_settings: list[tuple[str, int]] | list[tuple[str, str]]) -> None:
        """program mux with failure recovery logic. returns nothing but raises a value error on failure"""
        if self.enabled:
            if not self.set_mux_attempt(mux_settings):
                self.lg.debug("Trying to recover from mux set error")
                got_muxes = self.probe_muxes()  # run the mux probe code that will reset the hardware and check for individual mux IC comms
                if not self.expect_empty("s", tries=3):  # deselect everything
                    err_msg = f'Failure clearing mux during recovery attempt. MUX presence: "{got_muxes}", expected "{self.expected_muxes}"'
                    self.lg.error(err_msg)
                    raise ValueError(err_msg)
                else:
                    if self.set_mux_attempt(mux_settings):
                        self.lg.debug("Sucessful recovery from mux set error")
                    else:
                        self.expect_empty("s", tries=3)
                        err_msg = f'Unable to set MUX. MUX presence: "{got_muxes}", expected "{self.expected_muxes}". Attempt: "{mux_settings}"'
                        self.lg.error(err_msg)
                        raise ValueError(err_msg)

    def set_mux_attempt(self, mux_settings: list[tuple[str, int]] | list[tuple[str, str]]):
        """attempts to program mux, returns success bool"""
        success = False
        if self.enabled:
            for mux_setting in mux_settings:
                slot, pad = mux_setting
                # TODO: take into account the configured mux type here (from address)
                if isinstance(pad, int):
                    dlp = self.snaith_mux_pixel_lookup[pad]
                else:
                    dlp = pad  # the pad designator was given in direct latch programming syntax
                if slot.startswith("EXT") or (slot == "OFF"):
                    dlp = ""
                    slot = ""
                mux_string = f"s{slot}{dlp}"
                if not self.expect_empty(mux_string, tries=3):
                    break  # abort on failure
            else:
                success = True
        else:
            success = True
        return success

    @staticmethod
    def set_keepalive_linux(sock, after_idle_sec=1, interval_sec=3, max_fails=5):
        """Set TCP keepalive on an open socket.

        It activates after 1 second (after_idle_sec) of idleness,
        then sends a keepalive ping once every 3 seconds (interval_sec),
        and closes the connection after 5 failed ping (max_fails), or 15 seconds
        """
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, after_idle_sec)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, interval_sec)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, max_fails)

    @staticmethod
    def set_keepalive_osx(sock, after_idle_sec=1, interval_sec=3, max_fails=5):
        """Set TCP keepalive on an open socket.

        sends a keepalive ping once every 3 seconds (interval_sec)
        """
        # scraped from /usr/include, not exported by python's socket module
        TCP_KEEPALIVE = 0x10
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, TCP_KEEPALIVE, interval_sec)


# testing
if __name__ == "__main__":
    pcb_address = "10.46.0.239"
    with MC(pcb_address, timeout=1) as p:
        print("MC connection initiated")
        print(f"MC firmware version: {p.firmware_version}")
        print(f"MC axes: {p.detected_axes}")
        print(f"MC muxes: {p.detected_muxes}")
