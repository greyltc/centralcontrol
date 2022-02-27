from .wavelabs import Wavelabs

# from .newport import Newport
import os
import threading

import sys
import logging

# for logging directly to systemd journal if we can
try:
    import systemd.journal
except ImportError:
    pass


class Illumination(object):
    """
    generic class for handling a light source
    only supports wavelabs and newport via USB (ftdi driver)
    """

    light_engine = None
    protocol = None
    connection_timeout = 10  # s. wait this long for wavelabs comms connection to form
    comms_timeout = 1  # s. wait this long for an ack from wavelabs for any communication
    barrier_timeout = 10  # s. wait at most this long for thread sync on light state change
    _current_state = False  # True if we believe the light is on, False if we believe it's off
    requested_state = False  # keeps track of what state we'd like the light to be in

    def __init__(self, address="", connection_timeout=10, comms_timeout=1):
        """sets up communication to light source"""
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

        self.connection_timeout = connection_timeout  # s
        self.comms_timeout = comms_timeout  # s
        self.request_on = False
        self.requested_state = False
        self.barrier = threading.Barrier(1, action=self.set_state, timeout=self.barrier_timeout)  # thing that blocks threads until they're in sync

        addr_split = address.split(sep="://", maxsplit=1)
        protocol = addr_split[0]
        if protocol.lower() == "env":
            env_var = addr_split[1]
            if env_var in os.environ:
                address = os.environ.get(env_var)
            else:
                raise ValueError("Environment Variable {:} could not be found".format(env_var))
            addr_split = address.split(sep="://", maxsplit=1)
            protocol = addr_split[0]

        if protocol.lower().startswith("wavelabs"):
            location = addr_split[1]
            ls = location.split(":")
            host = ls[0]
            if len(ls) == 1:
                port = None
            else:
                port = int(ls[1])
            if "relay" in protocol.lower():
                relay = True
            else:
                relay = False
            self.light_engine = Wavelabs(host=host, port=port, relay=relay, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
        # elif protocol.lower() == ('ftdi'):
        #  self.light_engine = Newport(address=address)
        self.protocol = protocol

        self.lg.debug(f"{__name__} initialized.")

    @property
    def n_sync(self):
        """how many threads we need to wait for to synchronize"""
        return self.barrier.parties

    @n_sync.setter
    def n_sync(self, value):
        """
        update the number of threads we need to wait for to consider ourselves *NSYNC
        setting this while waiting for synchronization will raise a barrier broken error
        """

        self.barrier.abort()

        # the thing that blocks threads until they're in sync for a light state change
        self.barrier = threading.Barrier(value, action=self.set_state, timeout=self.barrier_timeout)

    @property
    def on(self):
        """
        query the light's current state
        (might differe than requested state)
        """
        return self._current_state

    @on.setter
    def on(self, value):
        """set true when you wish the light to be on and false if you want it to be off"""
        if isinstance(value, bool):
            if value != self._current_state:
                self.lg.debug(f"Request to change light state to {value}. Waiting for synchronization...")
                self.requested_state = value
                try:
                    draw = self.barrier.wait()
                    if draw == 0:  # we're the lucky winner!
                        self.lg.debug(f"Light state synchronization complete!")
                except threading.BrokenBarrierError as e:
                    # most likely a timeout
                    # could also be if the barrier was reset or aborted during the wait
                    # or if the call to change the light state errored
                    raise ValueError(f"The light synchronization barrier was broken! {e}")
            else:
                # requested state matches actual state
                self.lg.debug(f"Light output is already {value}")
        else:
            self.lg.debug(f"Don't understand new light state request: {value=}")

    def set_state(self, force_state=None):
        """
        set illumination state based on self.requested_state
        should not be called directly (instead use the barrier-enabled on parameter)
        unless you call it with force_state True or False to bypass the barrier-based thread sync interface
        """
        call_state = None  # the state we'll be setting the light to
        ret = None
        if force_state is None:
            call_state = self.requested_state
        elif isinstance(force_state, bool):
            call_state = force_state
        else:
            raise ValueError(f"New light state setting invalid: {call_state=}")

        self.lg.debug(f"set_state {self.requested_state} called")
        if call_state:
            ret = self.light_engine.on()
        else:
            ret = self.light_engine.off()

        if (ret == 0) or (isinstance(ret, str) and ret.startswith("sn")):
            self._current_state = call_state
        else:
            self.lg.debug(f"failure to set the light's state")

        return ret

    def connect(self):
        """forms connection to light source"""
        self.lg.debug("ill connect() called")
        ret = self.light_engine.connect()
        self.lg.debug("ill connect() compelte")
        return ret

    def get_spectrum(self):
        """
        fetches a spectrum if the light engine supports it, assumes a recipe has been set
        """
        self.lg.debug("ill get_spectrum() called")
        spec = self.light_engine.get_spectrum()
        self.lg.debug("ill get_spectrum() complete")
        self.get_temperatures()  # just to trigger the logging
        return spec

    def disconnect(self):
        """
        clean up connection to light.
        this is called by it be called by __del__ so it might not need to be called in addition
        """
        self.lg.debug("ill disconnect() called")
        if hasattr(self, "light_engine"):
            self.light_engine.disconnect()
            del self.light_engine  # prevents disconnect from being called more than once
        self.lg.debug("ill disconnect() complete")

    def set_recipe(self, recipe_name=None):
        """
        sets the active recipe, None will use the default recipe
        """
        self.lg.debug(f"ill set_recipe({recipe_name=}) called")
        ret = self.light_engine.activate_recipe(recipe_name)
        self.lg.debug("ill set_recipe() complete")
        return ret

    def set_runtime(self, ms):
        """
        sets the recipe runtime in ms
        """
        self.lg.debug(f"ill set_runtime({ms=}) called")
        ret = self.light_engine.set_runtime(ms)
        self.lg.debug("ill set_runtime() complete")
        return ret

    def get_runtime(self):
        """
        gets the recipe runtime in ms
        """
        self.lg.debug("ill get_runtime() called")
        runtime = self.light_engine.get_runtime()
        self.lg.debug(f"ill get_runtime() complete with {runtime=}")
        return runtime

    def set_intensity(self, percent):
        """
        sets the recipe runtime in ms
        """
        self.lg.debug(f"ill set_intensity({percent=}) called")
        ret = self.light_engine.set_intensity(percent)
        self.lg.debug("ill set_intensity() complete")
        return ret

    def get_intensity(self):
        """
        gets the recipe runtime in ms
        """
        self.lg.debug("ill get_intensity() called")
        intensity = self.light_engine.get_intensity()
        self.lg.debug(f"ill get_intensity() complete with {intensity=}")
        return intensity

    def get_run_status(self):
        """
        gets the light engine's run status, expected to return either "running" or "finished"
        also updates on parmater via _current_state
        """
        self.lg.debug("ill get_run_status() called")
        status = self.light_engine.get_run_status()
        if status == "running":
            self._current_state = True
        else:
            self._current_state = False
        self.lg.debug(f"ill get_run_status() complete with {status=}")
        return status

    def get_temperatures(self):
        """
        returns a list of light engine temperature measurements
        """
        self.lg.debug("ill get_temperatures() called")
        temp = []
        if "wavelabs" in self.protocol:
            temp.append(self.light_engine.get_vis_led_temp())
            temp.append(self.light_engine.get_ir_led_temp())
        self.lg.debug(f"ill get_temperatures() complete with {temp=}")
        return temp

    def __del__(self):
        """
        clean up connection to light engine
        put this in __del__ to esure it gets called
        """
        self.lg.debug("ill __del__() called")
        self.disconnect()
        self.lg.debug("ill __del__() complete")
