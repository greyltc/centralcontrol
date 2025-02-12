#!/usr/bin/env python3

import usbtmc
import time
try:
    from centralcontrol.logstuff import get_logger
except:
    pass


class Usbtmclight(object):
    """interface for a light source with usb-tmc comms (like LSS-7120)"""

    idn: str
    address:str = "USB::0x1fde::0x000a::INSTR"  # "USB::0x1fde::0x000a::INSTR" for LSS-7120
    is_LSS_7120:bool = False
    tmc_obj:usbtmc.Instrument|None = None
    _lit:bool|None = None
    stb:int|None = None  # status byte
    under_temperature = False
    over_temperature = False
    # status byte for LSS-7120
    # A decimal number containing bit-encoded information:
    # If bit 0 is set, the output is on.
    # Bit 1: reserved
    # If bit 2 is set, the head is disconnected (check the cable).
    # If bit 3 is set, the head has not yet warmed up to its optimum operating temperature.
    # If bit 4 is set, the head temperature is above its optimum operating range.

    # extra stuff needed to be compatible with the LightAPI
    runtime = 60000  # compat
    _intensity: float = 0.0  # compat
    _on_intensity: float = 100.0  # compat
    active_recipe: None | str = None  # compat
    last_temps = (-99.9, -99.9)


    def __init__(self, *args, **kwargs):
        """
        sets up the usbtmclight comms object
        """
        try:
            self.lg = get_logger(".".join([__name__, type(self).__name__]))
        except:
            self.lg = None

        # compat
        if "active_recipe" in kwargs:
            self.active_recipe = kwargs["active_recipe"]

        # compat
        if "intensity" in kwargs:
            self._on_intensity = kwargs["intensity"]

        if "address" in kwargs:
            self.address = kwargs["address"]

        if "1fde" in self.address and "000a" in self.address:
            self.is_LSS_7120 = True

        if self.lg:
            self.lg.debug("Initialized.")


    def connect(self):
        """
        generic connect method, does what's appropriate for getting comms up, timeouts are in seconds
        0 is success
        -3 is programming/logic error
        """
        ret = -3
        if self.lg:
            self.lg.debug(f"Connecting to {self.address}")
        self.tmc_obj = usbtmc.Instrument(self.address)
        self.tmc_obj.open()

        # comms immediately after open seem unreliable, so we do this
        status_tries = 10
        while status_tries and self.stb is None:
            self.get_status()
            status_tries -= 1

        self.idn = self.tmc_obj.ask("*IDN?")  # type: ignore
        if self.lg:
            self.lg.debug(f"Connected to {self.idn}")
        self.query_on_state()
        ret = 0
        return ret

    def disconnect(self, *args, **kwargs):
        """do our best to clean up and tear down connection"""
        if self.lg:
            self.lg.debug(f"Disconnecting from {self.tmc_obj} at {self.address}")

        if "turn_off" in kwargs:
            turn_off = kwargs["turn_off"]
        else:
            turn_off = True

        if turn_off:
            try:
                self.off()
            except Exception as e:
                pass

        try:
            self.tmc_obj.close()
            if self.lg:
                self.lg.debug(f"Connected: {self.tmc_obj.connected}")
        except Exception as e:
            if self.lg:
                self.lg.debug(f"Bad disconnection: {e}")
            
        return None

    def on(self):
        """turns the light on"""
        #self._intensity = self._on_intensity  # compat
        self.tmc_obj.write("output 1")
        self.get_status()
        if self.query_on_state():
            ret = 0
        else:
            ret = -1
        if self.lg:
            if ret == 0:
                self.lg.debug(f"Light on")
            else:
                self.lg.debug(f"Light on fail")
        if ret == 0:
            ret = "sn343"  # compat
        return ret

    def off(self):
        """turns the light off"""
        #self._intensity = 0  # compat
        self.tmc_obj.write("output 0")
        self.get_status()
        if self.query_on_state():
            ret = -1
        else:
            ret = 0
        if self.lg:
            if ret == 0:
                self.lg.debug(f"Light off")
            else:
                self.lg.debug(f"Light off fail")
        return ret
    
    def set_state(self, state:bool):
        """sets the light's state"""
        if state:
            ret = self.on()
            if isinstance(ret, str):
                ret = 0
        else:
            ret = self.off()
        return ret
    
    def query_on_state(self):
        if self.tmc_obj.ask("output?") == "1":
            self._lit = True
            self._intensity = self._on_intensity
        else:
            self._lit = False
            self._intensity = 0
        return self._lit
    
    def get_status(self):
        if self.is_LSS_7120:
            try:
                # hack for LSS-7120 not supporting read_stb()
                self.stb = int(self.tmc_obj.ask("status?"))
            except:
                self.stb = None
        else:
            self.stb = self.tmc_obj.read_stb()

        if self.is_LSS_7120:
            try:
                if self.stb & 0b1000:
                    self.under_temperature = True  # bit 3 is "too cold"
                else:
                    self.under_temperature = False

                if self.stb & 0b10000:
                    self.over_temperature = True
                else:
                    self.over_temperature = False
            except:
                pass

        if self.lg:
            if self.stb is not None:
                self.lg.debug(f"Status byte: {bin(self.stb)}")
            else:
                self.lg.debug(f"Status byte: {self.stb}")
            if self.over_temperature:
                self.lg.warning(f"The light source is too hot!")
            if self.under_temperature:
                self.lg.info(f"The light source isn't warmed up.")
        return self.stb
    
    def get_run_status(self):
        """compat"""
        if self._lit:
            ret = "running"
        else:
            ret = "finished"
        return ret
    
    def set_runtime(self, ms):
        """compat"""
        self.runtime = ms
        return 0

    def get_runtime(self):
        """compat"""
        return self.runtime

    def set_intensity(self, percent):
        """compat"""
        self._intensity = percent
        return 0

    def get_intensity(self):
        """compat"""
        return self._intensity
    
    def get_temperatures(self, *args, **kwargs):
        if self.get_status() is not None:
            if self.over_temperature:
                ret = (999.9, 999.9)
            elif self.under_temperature:
                ret = (0.0, 0.0)
            else:
                ret = (25.3, 17.3)  # "good" values
        else:
            ret = (-999.9, -999.9)
        self.last_temps = ret
        return ret

    def activate_recipe(self, recipe_name=None):
        """compat"""
        if recipe_name is not None:
            self.active_recipe = recipe_name
        return 0

if __name__ == "__main__":
    """do baseline testing"""
    import time

    class ATestingAPI(object):
        def __init__(self, address="USB::0x1fde::0x000a::INSTR", *args, **kwargs) -> None:

            # store away the init args and kwargs
            self.init_args = args
            self.init_kwargs = kwargs

            self.tst_obj = Usbtmclight(address=address)

        def __enter__(self) -> "ATestingAPI":
            """so that the light can enter a context"""
            print(f"Connecting to {self.tst_obj.address}")
            self.tst_obj.connect()
            print(f"Connected to {self.tst_obj.idn}")
            if self.tst_obj.stb is not None:
                print(f"Status byte: {bin(self.tst_obj.stb)}")
            else:
                print(f"Status byte: {self.tst_obj.stb}")
            if self.tst_obj.query_on_state():
                print("and it's on")
            else:
                print("and it's off")
            return self

        def __exit__(self, exc_type, exc_value, traceback) -> bool:
            """so that the smu can leave a context cleanly"""
            print(f"Disconnecting from {self.tst_obj.tmc_obj} at {self.tst_obj.address}")
            self.tst_obj.disconnect(turn_off=True)
            print(f"Connected: {self.tst_obj.tmc_obj.connected}")
            return False
        
        @property
        def lit(self) -> bool:
            return self.tst_obj.query_on_state()

        @lit.setter
        def lit(self, value: bool):
            self.tst_obj.set_state(value)
            return None

    on_duration_s = 5.002  # seconds, should be more than 3 here
    light_address = "USB::0x1fde::0x000a::INSTR"  # LSS-7120

    with ATestingAPI(light_address) as light:
        print("Light turns on in...")
        time.sleep(1)
        print("3...")
        time.sleep(1)
        print("2...")
        time.sleep(1)
        print("1...")
        time.sleep(1)
        print("Now!")
        light.lit = True
        print(f"{light.lit=}")
        print(f"Light turns off in {on_duration_s} [s]")
        time.sleep(on_duration_s - 3)
        print("3...")
        time.sleep(1)
        print("2...")
        time.sleep(1)
        print("1...")
        time.sleep(1)
        print("Now!")
        light.lit = False
        print(f"{light.lit=}")

    print("Done!")
