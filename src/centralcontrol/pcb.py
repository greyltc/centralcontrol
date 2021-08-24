#!/usr/bin/env python3

from telnetlib import Telnet
import socket
import time
import os

import sys
import logging

# for logging directly to systemd journal if we can
try:
    import systemd.journal
except ImportError:
    pass


class Pcb(object):
    """Interface for talking to the control PCB"""

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
    detected_muxes = []
    detected_axes = []
    expected_muxes = []
    welcome_message = None

    class MyTelnet(Telnet):
        def read_response(self, timeout=None):
            found_prompt = False
            resp = self.read_until(Pcb.prompt, timeout=timeout)
            if resp.endswith(Pcb.prompt):
                found_prompt = True
            ret = resp.rstrip(Pcb.prompt).decode().strip()
            if len(resp) == 0:
                ret = None  # nothing came back (likely a timeout)
            return ret, found_prompt

        def send_cmd(self, cmd):
            if not cmd.endswith(Pcb.write_terminator.decode()):
                self.write(cmd.encode())
            else:
                self.write(cmd.encode() + Pcb.write_terminator)
            self.sock.sendall()

    def __init__(self, address=None, timeout=comms_timeout, expected_muxes=[]):
        self.comms_timeout = timeout  # pcb has this many seconds to respond

        # setup logging
        self.lg = logging.getLogger(__name__)
        self.lg.setLevel(logging.DEBUG)

        if not self.lg.hasHandlers():
            # set up logging to systemd's journal if it's there
            if "systemd" in sys.modules:
                sysdl = systemd.journal.JournalHandler(SYSLOG_IDENTIFIER=self.lg.name)
                sysLogFormat = logging.Formatter(("%(levelname)s|%(message)s"))
                sysdl.setFormatter(sysLogFormat)
                self.lg.addHandler(sysdl)
            else:
                # for logging to stdout & stderr
                ch = logging.StreamHandler()
                logFormat = logging.Formatter(("%(asctime)s|%(name)s|%(levelname)s|%(message)s"))
                ch.setFormatter(logFormat)
                self.lg.addHandler(ch)

        if address is not None:
            addr_split = address.split(":")
            if len(addr_split) == 1:
                self.telnet_host = addr_split[0]
            else:
                h, p = address.split(":")
                self.telnet_host = h
                self.telnet_port = int(p)

        self.expected_muxes = expected_muxes

        self.lg.debug(f"{__name__} initialized.")

    def __enter__(self):
        self.connect()
        return self

    # figures out what muxes are connected
    def probe_muxes(self):
        mux_int = int(self.query("c"))
        mux_bin_str = f"{mux_int:b}"
        mux_bin_str_rev = mux_bin_str[::-1]
        self.detected_muxes = []
        start_char = "A"
        for i, b in enumerate(mux_bin_str_rev):
            if b == "1":
                self.detected_muxes.append(chr(ord(start_char) + i))

    # figures out what axes are connected

    def probe_axes(self):
        axes_int = int(self.query("e"))
        axes_bin_str = f"{axes_int:b}"
        axes_bin_str_rev = axes_bin_str[::-1]
        self.detected_axes = []
        start_char = "1"
        for i, b in enumerate(axes_bin_str_rev):
            if b == "1":
                self.detected_axes.append(chr(ord(start_char) + i))

    def __exit__(self, type, value, traceback):
        self.disconnect()

    def connect(self):
        """connects to control PCB"""
        connection_retries = 2
        for attempt in range(connection_retries):
            self.tn = self.MyTelnet(self.telnet_host, self.telnet_port, timeout=self.comms_timeout)
            self.sf = self.tn.sock.makefile("rwb", buffering=0)
            self.tn.sock.settimeout(self.comms_timeout)

            if "ix" in os.name:
                Pcb.set_keepalive_linux(self.tn.sock)  # let's try to keep our connection alive!

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
        try:
            self.write(self, method)
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
        if not cmd.endswith(self.write_terminator):
            cmd = cmd + self.write_terminator

        self.sf.write(cmd.encode())
        self.sf.flush()

    # query with no ack check
    def query_nocheck(self, query):
        self.write(query)
        return self.tn.read_response(timeout=self.comms_timeout)

    # query with better error handling and with ack check
    def query(self, query):
        answer = None
        ack = False
        try:
            answer, ack = self.query_nocheck(query)
        except Exception:
            self.lg.warning(f"Firmware comms failure while trying to send '{query}'")
        if ack == False:
            self.lg.warning(f"Firmware did not acknowledge '{query}'")
        return answer

    def expect_empty(self, cmd):
        """sends a command that we expect an empty response to"""
        try:
            rslt = self.query(cmd)
        except:
            rslt = None

        if rslt == "":
            success = True
        else:
            success = False
            self.lg.warning(f"Unexpected non-empty response from PCB: {cmd=} --> {rslt=}")
        return success

    def expect_int(self, cmd):
        """sends a command that we expect an intiger response to"""
        pcb_ans = None
        rslt = None
        try:
            pcb_ans = self.query(cmd)
            rslt = int(pcb_ans)
        except Exception as e:
            self.lg.debug(f"Unexpected non-int response from PCB: {cmd=} --> {pcb_ans=}")
        return rslt

    # configures the mux
    def set_mux(self, mux_setting):
        for mux_string in mux_setting:
            resp = self.query(mux_string)
            if resp != "":  # break on error setting mux message
                self.lg.warning(f"MUX response to {mux_string} was '{resp}'")
                break
        return resp

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
    with Pcb(pcb_address, timeout=1) as p:
        print("Controller connection initiated")
        print(f"Controller firmware version: {p.firmware_version}")
        print(f"Controller axes: {p.detected_axes}")
        print(f"Controller muxes: {p.detected_muxes}")
