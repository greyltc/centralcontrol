import mpmath
import os
import time
import numpy


class motion:
    substrate_centers = [
        300,
        260,
        220,
        180,
        140,
        100,
        60,
        20,
    ]  # mm from home to the centers of A, B, C, D, E, F, G, H substrates
    photodiode_location = 315  # mm

    def __init__(self, address="", pcb_object=None):
        """
    sets up communication to motion controller
    """
        self.address = address
        self.p = pcb_object

    def connect(self):
        print("Connected to virtual motion controller")

    def move(self, mm):
        return 0

    def goto(self, mm):
        return 0

    def home(self):
        return 0

    def estop(self):
        return 0

    def get_position(self):
        return []


class illumination:
    def __init__(self, address=""):
        """
    sets up communication to light source
    """
        print(address)
        addr_split = address.split(sep="://", maxsplit=1)
        protocol = addr_split[0]
        print(protocol)
        if protocol.lower() == "env":
            env_var = addr_split[1]
            if env_var in os.environ:
                address = os.environ.get(env_var)
            else:
                raise ValueError(
                    "Environment Variable {:} could not be found".format(env_var)
                )
            addr_split = address.split(sep="://", maxsplit=1)
            protocol = addr_split[0]

        if protocol.lower().startswith("wavelabs"):
            self.light_engine = wavelabs(address=address)
            self.wavelabs = True
        elif protocol.lower() == ("ftdi"):
            self.light_engine = Newport(address=address)

    def connect(self):
        print("Connected to virtual lightsource")

    def activateRecipe(self, recipe):
        print("Light engine recipe '{:}' virtually activated.".format(recipe))

    def on(self):
        print("Virtual light turned on")

    def off(self):
        print("Virtual light turned off")

    def close(self):
        pass

    def disconnect(self):
        print("Disconnecting light source")


class wavelabs:
    """interface to the wavelabs LED solar simulator"""

    iseq = 0  # sequence number for comms with wavelabs software
    protocol = "wavelabs"  # communication method for talking to the wavelabs light engine, wavelabs for direct, wavelabs-relay for relay
    default_recipe = "am1_5_1_sun"
    port = 3334  # 3334 for direct connection, 3335 for through relay service
    host = (
        "0.0.0.0"  # 0.0.0.0 for direct connection, localhost for through relay service
    )

    def __init__(self, address="wavelabs://0.0.0.0:3334"):
        """
    sets up the wavelabs object
    address is a string of the format:
    wavelabs://listen_ip:listen_port (should probably be wavelabs://0.0.0.0:3334)
    or
    wavelabs-relay://host_ip:host_port (should probably be wavelabs-relay://localhost:3335)
    
    """
        self.protocol, location = address.split("://")
        self.host, self.port = location.split(":")
        self.port = int(self.port)

    def recvXML(self):
        """reads xml object from socket"""
        pass

    def startServer(self):
        """define a server which listens for the wevelabs software to connect"""
        pass

    def connect(self):
        """
        generic connect method, does what's appropriate for getting comms up based on self.protocol
        """
        if self.protocol == "wavelabs":
            print("connected to wavelabs")
        elif self.protocol == "wavelabs-relay":
            print("connected to wavelabs relay")
        else:
            print(
                "WRNING: Got unexpected wavelabs comms protocol: {:}".format(
                    self.protocol
                )
            )

    def disconnect(self):
        """Disconnect server."""
        print("disconnecting wavelabs server")

    def awaitConnection(self):
        """returns once the wavelabs program has connected"""
        pass

    def connectToRelay(self):
        """forms connection to the relay server"""
        pass

    def startFreeFloat(
        self,
        time=0,
        intensity_relative=100,
        intensity_sensor=0,
        channel_nums=["8"],
        channel_values=[50.0],
    ):
        """starts/modifies/ends a free-float run"""
        pass

    def activateRecipe(self, recipe_name=default_recipe):
        """activate a solar sim recipe by name"""
        print(f"activating recipe: {recipe_name}")

    def waitForResultAvailable(self, timeout=10000, run_ID=None):
        """wait for result from a recipe to be available"""
        pass

    def waitForRunFinished(self, timeout=10000, run_ID=None):
        """wait for the current run to finish"""
        pass

    def getRecipeParam(
        self, recipe_name=default_recipe, step=1, device="Light", param="Intensity"
    ):
        return 1

    def getDataSeries(
        self,
        step=1,
        device="LE",
        curve_name="Irradiance-Wavelength",
        attributes="raw",
        run_ID=None,
    ):
        """returns a data series from SinusGUI"""
        ret = [
            {
                "data": {
                    "Wavelenght": [300, 400, 500, 600, 700, 800, 900, 1100],
                    "Irradiance": [1, 1, 1, 1, 1, 1, 1, 1],
                }
            }
        ]

        return ret

    def setRecipeParam(
        self,
        recipe_name=default_recipe,
        step=1,
        device="Light",
        param="Intensity",
        value=100.0,
    ):
        pass

    def on(self):
        """starts the last activated recipe"""
        return 1

    def off(self):
        """cancel a currently running recipe"""
        pass

    def exitProgram(self):
        """closes the wavelabs solar sim program on the wavelabs PC"""
        print("Exiting WaveLabs program")


class k2400:
    """Solar cell device simulator (looks like k2400 class)
  """

    def __init__(self):
        self.idn = "Virtual Sourcemeter"
        self.t0 = time.time()
        self.measurementTime = 0.01  # [s] the time it takes the simulated sourcemeter to make a measurement

        self.Rs = 9.28  # [ohm]
        self.Rsh = 1e6  # [ohm]
        self.n = 3.58
        self.I0 = 260.4e-9  # [A]
        self.Iph = 6.293e-3  # [A]
        self.cellTemp = 29  # degC
        self.T = 273.15 + self.cellTemp  # cell temp in K
        self.K = 1.3806488e-23  # boltzman constant
        self.q = 1.60217657e-19  # electron charge
        self.Vth = mpmath.mpf(self.K * self.T / self.q)  # thermal voltage ~26mv
        self.V = 0  # voltage across device
        self.I = 0  # current through device
        self.updateCurrent()

        # for sweeps:
        self.sweepMode = False
        self.nPoints = 1001
        self.sweepStart = 1
        self.sweepEnd = 0

        self.status = 0

        self.src = "VOLT"

    def setNPLC(self, nplc):
        return

    def setupDC(self, sourceVoltage=True, compliance=0.1, setPoint=1, senseRange="f"):
        if sourceVoltage:
            src = "voltage"
            snc = "current"
        else:
            src = "current"
            snc = "voltage"
        self.src = src
        self.write(":source:{:s} {:0.6f}".format(self.src, setPoint))
        self.sweepMode = False
        return

    def setupSweep(
        self,
        sourceVoltage=True,
        compliance=0.1,
        nPoints=101,
        start=0,
        end=1,
        senseRange="f",
    ):
        """setup for a sweep operation
    """
        # sm = self.sm
        if sourceVoltage:
            src = "voltage"
            snc = "current"
        else:
            src = "current"
            snc = "voltage"
        self.src = src
        self.nPoints = int(nPoints)
        self.sweepMode = True
        self.sweepStart = start
        self.sweepEnd = end
        self.dV = self.query_values(":source:voltage:step?")

    def setOutput(self, outVal):
        self.write(":source:{:s} {:.6f}".format(self.src, outVal))

    def outOn(self, on=True):
        return

    # the device is open circuit
    def openCircuitEvent(self):
        self.I = 0
        Rs = self.Rs
        Rsh = self.Rsh
        n = self.n
        I0 = self.I0
        Iph = self.Iph
        Vth = self.Vth
        Voc = (
            I0 * Rsh
            + Iph * Rsh
            - Vth
            * n
            * mpmath.lambertw(
                I0 * Rsh * mpmath.exp(Rsh * (I0 + Iph) / (Vth * n)) / (Vth * n)
            )
        )
        self.V = float(numpy.real_if_close(numpy.complex(Voc)))

    # recompute device current
    def updateCurrent(self):
        Rs = self.Rs
        Rsh = self.Rsh
        n = self.n
        I0 = self.I0
        Iph = self.Iph
        Vth = self.Vth
        V = self.V
        I = (
            Rs * (I0 * Rsh + Iph * Rsh - V)
            - Vth
            * n
            * (Rs + Rsh)
            * mpmath.lambertw(
                I0
                * Rs
                * Rsh
                * mpmath.exp(
                    (Rs * (I0 * Rsh + Iph * Rsh - V) / (Rs + Rsh) + V) / (Vth * n)
                )
                / (Vth * n * (Rs + Rsh))
            )
        ) / (Rs * (Rs + Rsh))
        self.I = float(-1 * numpy.real_if_close(numpy.complex(I)))

    def write(self, command):
        if ":source:current " in command:
            currentVal = float(command.split(" ")[1])
            if currentVal == 0:
                self.openCircuitEvent()
            else:
                raise ValueError("Can't do currents other than zero right now!")
        elif command == ":source:voltage:mode sweep":
            self.sweepMode = True
        elif command == ":source:voltage:mode fixed":
            self.sweepMode = False
        elif ":source:sweep:points " in command:
            self.nPoints = int(command.split(" ")[1])
        elif ":source:voltage:start " in command:
            self.sweepStart = float(command.split(" ")[1])
        elif ":source:voltage:stop " in command:
            self.sweepEnd = float(command.split(" ")[1])
        elif ":source:voltage " in command:
            self.V = float(command.split(" ")[1])
            self.updateCurrent()

    def query_ascii_values(self, command):
        return self.query_values(command)

    def read(self):
        return self.query_values("READ?")

    def query_values(self, command):
        if command == "READ?":
            if self.sweepMode:
                sweepArray = numpy.array([], dtype=numpy.float_).reshape(0, 4)
                voltages = numpy.linspace(self.sweepStart, self.sweepEnd, self.nPoints)
                for i in range(len(voltages)):
                    self.V = voltages[i]
                    self.updateCurrent()
                    time.sleep(self.measurementTime)
                    measurementLine = numpy.array(
                        [self.V, self.I, time.time() - self.t0, self.status]
                    )
                    sweepArray = numpy.vstack([sweepArray, measurementLine])
                return sweepArray.tolist()
            else:  # non sweep mode
                time.sleep(self.measurementTime)
                measurementLine = numpy.array(
                    [self.V, self.I, time.time() - self.t0, self.status]
                )
                return measurementLine.tolist()
        elif command == ":source:voltage:step?":
            dV = (self.sweepEnd - self.sweepStart) / self.nPoints
            return abs(dV)
        else:
            raise ValueError("What?")

    def measureUntil(
        self,
        t_dwell=numpy.inf,
        measurements=numpy.inf,
        cb=lambda x: None,
        handler=None,
        handler_kwargs={},
    ):
        """Meakes measurements until termination conditions are met
    supports a callback after every measurement
    returns a queqe of measurements
    """
        i = 0
        t_end = time.time() + t_dwell
        data = []
        while (i < measurements) and (time.time() < t_end):
            i = i + 1
            measurement = self.measure()
            data.append(measurement)
            cb(measurement)
            if handler is not None:
                handler(measurement, **handler_kwargs)
        return data

    def measure(self):
        return self.query_values("READ?")

    def close(self):
        pass

    def setTerminals(self, front=True):
        print(f"Front terminals: {front}")

    def setWires(self, twoWire=True):
        print(f"Two wire: {twoWire}")

    def disconnect(self):
        print("Disconnecting SMU")

    def setStepDelay(self, stepDelay=-1):
        pass


class pcb:
    """Mux and stage controller."""

    def __init__(
        self, address, ignore_adapter_resistors=True, timeout=1,
    ):
        self.timeout = timeout  # pcb has this many seconds to respond
        self.ignore_adapter_resistors = ignore_adapter_resistors

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        pass

    def substrateSearch(self):
        """Returns bitmask of connected MUX boards
    """
        found = 0x01

        return found

    def pix_picker(self, substrate, pixel, suppressWarning=False):
        return 0

    def write(self, cmd):
        pass

    def query(self, query):
        return 0

    def get(self, cmd):
        """
    sends cmd to the pcb and returns the relevant command response
    """
        return 0

    def getADCCounts(self, chan):
        """makes adc readings.
    chan can be 0-7 to directly read the corresponding adc channel
    """
        return 1

    def disconnect_all(self):
        """ Opens all the switches
    """
        pass

    def set_keepalive_linux(self, sock, after_idle_sec=1, interval_sec=3, max_fails=5):
        """Set TCP keepalive on an open socket.

    It activates after 1 second (after_idle_sec) of idleness,
    then sends a keepalive ping once every 3 seconds (interval_sec),
    and closes the connection after 5 failed ping (max_fails), or 15 seconds
    """
        pass

    def set_keepalive_osx(self, sock, after_idle_sec=1, interval_sec=3, max_fails=5):
        """Set TCP keepalive on an open socket.

    sends a keepalive ping once every 3 seconds (interval_sec)
    """
        pass
