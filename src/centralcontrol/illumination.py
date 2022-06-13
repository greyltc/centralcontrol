from centralcontrol.wavelabs import Wavelabs
from centralcontrol.virt import FakeLight

# from centralcontrol.newport import Newport

try:
    from centralcontrol.logstuff import get_logger as getLogger
except:
    from logging import getLogger

import os
import threading
import typing


def factory(cfg: typing.Dict) -> typing.Type["LightAPI"]:
    """light class factory
    give it a light source configuration dictionary and it will return the correct class to use
    """
    lg = getLogger(__name__)  # setup logging
    if "kind" in cfg:
        kind = cfg["kind"]
    else:
        lg.info("Assuming wavelabs type light")
        kind = "wavelabs"

    base = FakeLight  # the default is to make a virtual light
    if ("virtual" in cfg) and (cfg["virtual"] is False):
        if "wavelabs" in kind:
            base = Wavelabs  # wavelabs selected

    if ("enabled" in cfg) and (cfg["enabled"] is False):
        base = DisabledLight  # disabled SMU selected

    name = LightAPI.__name__
    bases = (LightAPI, base)
    # tdict = ret.__dict__.copy()
    tdict = {}
    return type(name, bases, tdict)  # return the configured light class overlayed with our API


class LightAPI(object):
    """unified light programming interface"""

    barrier: threading.Barrier
    barrier_timeout = 10  # s. wait at most this long for thread sync on light state change
    _current_intensity: int = 0  # percent. 0 means off. otherwise can be on [10, 100]. what we believe the light's intensity is
    requested_intensity: int = 0  # percent. 0 means off. otherwise can be on [10, 100]. keeps track of what we want the light's intensity to be
    on_intensity = None  # the intensity value the hardware was initalized with. used in "on"

    conn_status: int = -99  # connection status
    idn: str  # identification string

    def __init__(self, **kwargs) -> None:
        """just sets class variables"""
        self.lg = getLogger(".".join([__name__, type(self).__name__]))  # setup logging
        if "intensity" in kwargs:
            self.on_intensity = int(kwargs["intensity"])  # use this initial intensity for the "on" value

        # thing that blocks to ensure sync
        self.barrier = threading.Barrier(1, action=self.apply_intensity, timeout=self.barrier_timeout)

        super(LightAPI, self).__init__(**kwargs)

    def __enter__(self) -> "LightAPI":
        """so that the smu can enter a context"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        """so that the smu can leave a context cleanly"""
        self.disconnect()
        return False

    def connect(self) -> None:
        """connects to and initalizes hardware"""
        if self.conn_status < 0:
            self.conn_status = super(LightAPI, self).connect()  # call the underlying connect method
            if self.conn_status < 0:
                self.lg.debug(f"Connection attempt failed with status {self.conn_status}")
            else:
                # good connection, let's sync the light's state
                if super(LightAPI, self).get_run_status() == "running":
                    self._current_intensity = super(LightAPI, self).get_intensity()
                else:
                    self._current_intensity = 0  # the light is off
        return None

    def disconnect(self) -> None:
        """disconnect and clean up"""
        try:
            super(LightAPI, self).disconnect()  # call the underlying disconnect method
            self.conn_status = -80  # clean disconnection
        except Exception as e:
            self.conn_status = -89  # unclean disconnection
            self.lg.debug(f"Unclean disconnect: {e}")
        return None

    @property
    def lit(self) -> bool:
        return not (self.intensity == 0)

    @lit.setter
    def lit(self, value: bool):
        if value:
            if self.on_intensity is not None:
                setpoint = self.on_intensity
            else:
                setpoint = 100  # assume 100% if we don't know better
            self.intensity = setpoint
        else:
            self.intensity = 0
        return None

    @property
    def n_sync(self):
        """how many threads we need to wait for to synchronize"""
        return self.barrier.parties

    @n_sync.setter
    def n_sync(self, value: int) -> None:
        """
        update the number of threads we need to wait for to consider ourselves *NSYNC
        setting this while waiting for synchronization will raise a barrier broken error
        """

        self.barrier.abort()

        # thing that blocks to ensure sync
        self.barrier = threading.Barrier(value, action=self.apply_intensity, timeout=self.barrier_timeout)
        return None

    @property
    def intensity(self) -> int:
        """
        query the light's current intensity
        (might differ than requested intensity)
        """
        return self._current_intensity

    @intensity.setter
    def intensity(self, value: int) -> None:
        """set the intensity value you wish the light to be. ensures synchronization"""
        if isinstance(value, int) and ((value == 0) or ((value >= 10) and (value <= 100))):
            if value != self._current_intensity:
                self.lg.debug(f"Request to change light intensity to {value}. Waiting for synchronization...")
                self.requested_intensity = value
                try:
                    draw = self.barrier.wait()
                    if draw == 0:  # we're the lucky winner!
                        self.lg.debug(f"Light intensity synchronization complete!")
                    if value != self.requested_intensity:
                        self.lg.debug(f"Something's fishy. We wanted {value}, but got {self.requested_intensity}")
                except threading.BrokenBarrierError as e:
                    # most likely a timeout
                    # could also be if the barrier was reset or aborted during the wait
                    # or if the call to change the light state errored
                    raise ValueError(f"The light synchronization barrier was broken! {e}")
            else:
                # requested state matches actual state
                self.lg.debug(f"Light intensity is already {value}")
        else:
            self.lg.debug(f"Invalid light intensity request: {value=}")

    def apply_intensity(self, forced_intensity: int = None) -> int:
        """
        set illumination intensity based on self.requested_intensity
        should not be called directly (instead use the barrier-enabled on parameter)
        unless you call it with force_intensity to an int to bypass the barrier-based thread sync interface
        """
        ret = None
        setpoint = forced_intensity
        if setpoint is None:
            setpoint = self.requested_intensity

        self.lg.debug(f"apply_intensity() doing {setpoint}")
        case = "B"
        if case == "A":  # non-blinky mode
            if setpoint == 0:
                set_ret = 0
                on_ret = "sn"  # dummy to always pass check
                off_ret = super(LightAPI, self).off()
            else:
                off_ret = 0
                set_ret = super(LightAPI, self).set_intensity(setpoint)
                on_ret = super(LightAPI, self).on()
        else:  # blinky mode
            off_ret = super(LightAPI, self).off()
            set_ret = 0
            on_ret = "sn"
            if setpoint != 0:
                set_ret = super(LightAPI, self).set_intensity(setpoint)
                on_ret = super(LightAPI, self).on()

        if (isinstance(on_ret, str) and on_ret.startswith("sn")) and (set_ret == 0) and (off_ret == 0):
            self._current_intensity = setpoint
            ret = 0
        else:
            ret = -1
            self.lg.debug(f"failure to set the light's intensity: {off_ret=} and {set_ret=} and {on_ret=}")

        # if setpoint == 0:
        #    ret = 0  # dummy to always pass check
        #    off_ret = super(LightAPI, self).off()
        # else:
        #    off_ret = 0
        #    ret = super(LightAPI, self).set_intensity(setpoint)
        #    toggle_ret = super(LightAPI, self).on()

        # if (ret == 0) and (on_ret == 0) or (isinstance(toggle_ret, str) and toggle_ret.startswith("sn")):
        #    self._current_intensity = setpoint
        # else:
        #    ret = -1
        #    self.lg.debug(f"failure to set the light's intensity: {ret=} and {toggle_ret=}")

        return ret


# class Illumination(object):
#     """
#     generic class for handling a light source
#     only supports wavelabs and newport via USB (ftdi driver)
#     """

#     light_engine = None
#     protocol = None
#     connection_timeout = 10  # s. wait this long for wavelabs comms connection to form
#     comms_timeout = 1  # s. wait this long for an ack from wavelabs for any communication
#     barrier_timeout = 10  # s. wait at most this long for thread sync on light state change
#     _current_state = False  # True if we believe the light is on, False if we believe it's off
#     requested_state = False  # keeps track of what state we'd like the light to be in
#     last_temps = None
#     _current_intensity: int = 0  # percent. 0 means off. otherwise can be on [10, 100]. what we believe the light's intensity is
#     requested_intensity: int = 0  # percent. 0 means off. otherwise can be on [10, 100]. keeps track of what we want the light's intensity to be

#     def __init__(self, address="", connection_timeout=10, comms_timeout=1):
#         """sets up communication to light source"""
#         self.lg = getLogger(".".join([__name__, type(self).__name__]))  # setup logging

#         self.connection_timeout = connection_timeout  # s
#         self.comms_timeout = comms_timeout  # s
#         self.request_on = False
#         self.requested_state = False
#         self.barrier = threading.Barrier(1, action=self.set_state, timeout=self.barrier_timeout)  # thing that blocks threads until they're in sync
#         self.barrier2 = threading.Barrier(1, action=self.set_intensity, timeout=self.barrier_timeout)  # thing that blocks threads until they're in sync

#         addr_split = address.split(sep="://", maxsplit=1)
#         protocol = addr_split[0]
#         if protocol.lower() == "env":
#             env_var = addr_split[1]
#             if env_var in os.environ:
#                 address = os.environ.get(env_var)
#             else:
#                 raise ValueError("Environment Variable {:} could not be found".format(env_var))
#             addr_split = address.split(sep="://", maxsplit=1)
#             protocol = addr_split[0]

#         if protocol.lower().startswith("wavelabs"):
#             location = addr_split[1]
#             ls = location.split(":")
#             host = ls[0]
#             if len(ls) == 1:
#                 port = None
#             else:
#                 port = int(ls[1])
#             if "relay" in protocol.lower():
#                 relay = True
#             else:
#                 relay = False
#             self.light_engine = Wavelabs(host=host, port=port, relay=relay, connection_timeout=self.connection_timeout, comms_timeout=self.comms_timeout)
#         # elif protocol.lower() == ('ftdi'):
#         #  self.light_engine = Newport(address=address)
#         self.protocol = protocol

#         self.lg.debug("Initialized.")

#     @property
#     def n_sync(self):
#         """how many threads we need to wait for to synchronize"""
#         return self.barrier.parties

#     @n_sync.setter
#     def n_sync(self, value):
#         """
#         update the number of threads we need to wait for to consider ourselves *NSYNC
#         setting this while waiting for synchronization will raise a barrier broken error
#         """

#         self.barrier.abort()

#         # the thing that blocks threads until they're in sync for a light state change
#         self.barrier = threading.Barrier(value, action=self.set_state, timeout=self.barrier_timeout)

#     @property
#     def n_sync2(self):
#         """how many threads we need to wait for to synchronize"""
#         return self.barrier2.parties

#     @n_sync2.setter
#     def n_sync2(self, value):
#         """
#         update the number of threads we need to wait for to consider ourselves *NSYNC
#         setting this while waiting for synchronization will raise a barrier broken error
#         """

#         self.barrier2.abort()

#         # the thing that blocks threads until they're in sync for a light state change
#         self.barrier2 = threading.Barrier(value, action=self.set_intensity, timeout=self.barrier_timeout)

#     @property
#     def on(self):
#         """
#         query the light's current state
#         (might differ than requested state)
#         """
#         return self._current_state

#     @on.setter
#     def on(self, value):
#         """set true when you wish the light to be on and false if you want it to be off"""
#         if isinstance(value, bool):
#             if value != self._current_state:
#                 self.lg.debug(f"Request to change light state to {value}. Waiting for synchronization...")
#                 self.requested_state = value
#                 try:
#                     draw = self.barrier.wait()
#                     if draw == 0:  # we're the lucky winner!
#                         self.lg.debug(f"Light state synchronization complete!")
#                 except threading.BrokenBarrierError as e:
#                     # most likely a timeout
#                     # could also be if the barrier was reset or aborted during the wait
#                     # or if the call to change the light state errored
#                     raise ValueError(f"The light synchronization barrier was broken! {e}")
#             else:
#                 # requested state matches actual state
#                 self.lg.debug(f"Light output is already {value}")
#         else:
#             self.lg.debug(f"Don't understand new light state request: {value=}")

#     @property
#     def intensity(self):
#         """
#         query the light's current intensity
#         (might differ than requested intensity)
#         """
#         return self._current_intensity

#     @intensity.setter
#     def intensity(self, value: int):
#         """set the intensity value you wish the light to be"""
#         if isinstance(value, int):
#             if value != self._current_intensity:
#                 self.lg.debug(f"Request to change light intensity to {value}. Waiting for synchronization...")
#                 self.requested_intensity = value
#                 try:
#                     draw = self.barrier.wait()
#                     if draw == 0:  # we're the lucky winner!
#                         self.lg.debug(f"Light intensity synchronization complete!")
#                 except threading.BrokenBarrierError as e:
#                     # most likely a timeout
#                     # could also be if the barrier was reset or aborted during the wait
#                     # or if the call to change the light state errored
#                     raise ValueError(f"The light synchronization barrier was broken! {e}")
#             else:
#                 # requested state matches actual state
#                 self.lg.debug(f"Light intensity is already {value}")
#         else:
#             self.lg.debug(f"Don't understand new light intensity request: {value=}")

#     def set_intensity(self, force_intensity=None):
#         """
#         set illumination intensity based on self.requested_intensity
#         should not be called directly (instead use the barrier-enabled on parameter)
#         unless you call it with force_intensity to an int to bypass the barrier-based thread sync interface
#         """
#         ret = None
#         setpoint = force_intensity
#         if setpoint is None:
#             setpoint = self.requested_intensity
#         if not isinstance(setpoint, int):
#             raise ValueError(f"New light intensity setpoint is invalid: {setpoint=}")

#         self.lg.debug(f"set_intensity({self.requested_intensity}) called")
#         if setpoint == 0:
#             iret = 0  # no error
#             oret = self.light_engine.off()
#         else:
#             iret = self.light_engine.set_intensity(setpoint)
#             oret = self.light_engine.on()

#         if ((oret == 0) or ((isinstance(ret, str) and ret.startswith("sn")))) and (iret == 0):
#             self._current_intensity = setpoint
#         else:
#             self.lg.debug(f"failure to set the light's intensity")

#         return ret

#     def set_state(self, force_state: bool = None):
#         """
#         set illumination state based on self.requested_state
#         should not be called directly (instead use the barrier-enabled on parameter)
#         unless you call it with force_state True or False to bypass the barrier-based thread sync interface
#         """
#         ret = None
#         setpoint = force_state
#         if setpoint is None:
#             setpoint = self.requested_state
#         if not isinstance(setpoint, bool):
#             raise ValueError(f"New light state setting invalid: {setpoint=}")

#         self.lg.debug(f"set_state({setpoint}) called")
#         if setpoint:
#             ret = self.light_engine.on()
#         else:
#             ret = self.light_engine.off()

#         if (ret == 0) or ((isinstance(ret, str) and ret.startswith("sn"))):
#             self._current_state = setpoint
#         else:
#             self.lg.debug(f"failure to set the light's state")

#         return ret

#     def connect(self):
#         """forms connection to light source"""
#         self.lg.debug("ill connect() called")
#         ret = self.light_engine.connect()
#         self.lg.debug("ill connect() compelte")
#         return ret

#     def get_spectrum(self):
#         """
#         fetches a spectrum if the light engine supports it, assumes a recipe has been set
#         """
#         self.lg.debug("ill get_spectrum() called")
#         spec = self.light_engine.get_spectrum()
#         self.lg.debug("ill get_spectrum() complete")
#         self.get_temperatures()  # just to trigger the logging
#         return spec

#     def disconnect(self):
#         """
#         clean up connection to light.
#         this is called by it be called by __del__ so it might not need to be called in addition
#         """
#         self.lg.debug("ill disconnect() called")
#         if hasattr(self, "light_engine"):
#             self.light_engine.disconnect()
#             del self.light_engine  # prevents disconnect from being called more than once
#         self.lg.debug("ill disconnect() complete")

#     def set_recipe(self, recipe_name=None):
#         """
#         sets the active recipe, None will use the default recipe
#         """
#         self.lg.debug(f"ill set_recipe({recipe_name=}) called")
#         ret = self.light_engine.activate_recipe(recipe_name)
#         self.lg.debug("ill set_recipe() complete")
#         return ret

#     def set_runtime(self, ms):
#         """
#         sets the recipe runtime in ms
#         """
#         self.lg.debug(f"ill set_runtime({ms=}) called")
#         ret = self.light_engine.set_runtime(ms)
#         self.lg.debug("ill set_runtime() complete")
#         return ret

#     def get_runtime(self):
#         """
#         gets the recipe runtime in ms
#         """
#         self.lg.debug("ill get_runtime() called")
#         runtime = self.light_engine.get_runtime()
#         self.lg.debug(f"ill get_runtime() complete with {runtime=}")
#         return runtime

#     def set_intensity(self, percent):
#         """
#         sets the recipe runtime in ms
#         """
#         self.lg.debug(f"ill set_intensity({percent=}) called")
#         ret = self.light_engine.set_intensity(percent)
#         self.lg.debug("ill set_intensity() complete")
#         return ret

#     def get_intensity(self):
#         """
#         gets the recipe runtime in ms
#         """
#         self.lg.debug("ill get_intensity() called")
#         intensity = self.light_engine.get_intensity()
#         self.lg.debug(f"ill get_intensity() complete with {intensity=}")
#         return intensity

#     def get_run_status(self):
#         """
#         gets the light engine's run status, expected to return either "running" or "finished"
#         also updates on parmater via _current_state
#         """
#         self.lg.debug("ill get_run_status() called")
#         status = self.light_engine.get_run_status()
#         if status == "running":
#             self._current_state = True
#         else:
#             self._current_state = False
#         self.lg.debug(f"ill get_run_status() complete with {status=}")
#         return status

#     def get_temperatures(self):
#         """
#         returns a list of light engine temperature measurements
#         """
#         self.lg.debug("ill get_temperatures() called")
#         temp = []
#         if "wavelabs" in self.protocol:
#             temp.append(self.light_engine.get_vis_led_temp())
#             temp.append(self.light_engine.get_ir_led_temp())
#             self.last_temps = temp
#         self.lg.debug(f"ill get_temperatures() complete with {temp=}")
#         return temp

#     def __del__(self):
#         """
#         clean up connection to light engine
#         put this in __del__ to esure it gets called
#         """
#         self.lg.debug("ill __del__() called")
#         self.disconnect()
#         self.lg.debug("ill __del__() complete")


class DisabledLight(object):
    """this is the light class for when the user has disabled it"""

    def __init__(self, **kwargs):
        return None

    def __getattribute__(self, name):
        """handles any function call"""
        if hasattr(object, name):
            return object.__getattribute__(self, name)
        elif name == "idn":
            return "disabled"  # handle idn parameter check
        elif name == "connect":
            return lambda *args, **kwargs: 0  # connect() always returns zero
        else:
            return lambda *args, **kwargs: None  # all function calls return with none
