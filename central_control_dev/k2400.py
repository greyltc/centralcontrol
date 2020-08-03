#!/usr/bin/env python

import sys
import numpy as np
import time
from collections import deque
import pyvisa as visa
import warnings
import os


class k2400:
    """
  Intertace for Keithley 2400 sourcemeter
  """

    idnContains = "KEITHLEY"
    quiet = False
    idn = ""

    def __init__(
        self,
        visa_lib="@py",
        scan=False,
        addressString=None,
        terminator="\r",
        serialBaud=57600,
        front=False,
        twoWire=False,
        quiet=False,
    ):
        self.quiet = quiet
        self.readyForAction = False
        self.rm = self._getResourceManager(visa_lib)

        if scan:
            print(self.rm.list_resources())

        self.addressString = addressString
        self.terminator = terminator
        self.serialBaud = serialBaud
        self.sm = self._getSourceMeter(self.rm)
        self._setupSourcemeter(front=front, twoWire=twoWire)

    def __del__(self):
        try:
            g = self.sm.visalib.sessions[self.sm.session]
            g.close(g.interface.id)
            # self.sm.close()
        except:
            pass

    def _getResourceManager(self, visa_lib):
        try:
            rm = visa.ResourceManager(visa_lib)
        except:
            exctype, value1 = sys.exc_info()[:2]
            try:
                rm = visa.ResourceManager()
            except:
                exctype, value2 = sys.exc_info()[:2]
                print("Unable to connect to instrument.")
                print("Error 1 (using {:s} backend):".format(visa_lib))
                print(value1)
                print("Error 2 (using pyvisa default backend):")
                print(value2)
                raise ValueError("Unable to create a resource manager.")

        vLibPath = rm.visalib.get_library_paths()[0]
        if vLibPath == "unset":
            self.backend = "pyvisa-py"
        else:
            self.backend = vLibPath

        if not self.quiet:
            print("Using {:s} pyvisa backend.".format(self.backend))
        return rm

    def _getSourceMeter(self, rm):
        timeoutMS = 300  # initial comms timeout
        if "ASRL" in self.addressString:
            openParams = {
                "resource_name": self.addressString,
                "timeout": timeoutMS,
                "read_termination": self.terminator,
                "write_termination": self.terminator,
                "baud_rate": self.serialBaud,
                "flow_control": visa.constants.VI_ASRL_FLOW_XON_XOFF,
            }
            smCommsMsg = "ERROR: Can't talk to sourcemeter\nDefault sourcemeter serial comms params are: 57600-8-n with <CR> terminator and xon-xoff flow control."
        elif "GPIB" in self.addressString:
            openParams = {
                "resource_name": self.addressString,
                "write_termination": self.terminator,
            }  # , 'io_protocol': visa.constants.VI_HS488
            addrParts = self.addressString.split("::")
            board = addrParts[0][4:]
            address = addrParts[1]
            smCommsMsg = "ERROR: Can't talk to sourcemeter\nIs GPIB controller {:} correct?\nIs the sourcemeter configured to listen on address {:}?".format(
                board, address
            )
        elif ("TCPIP" in self.addressString) and ("SOCKET" in self.addressString):
            addrParts = self.addressString.split("::")
            host = addrParts[1]
            port = host = addrParts[2]
            openParams = {
                "resource_name": self.addressString,
                "timeout": timeoutMS,
                "read_termination": self.terminator,
                "write_termination": self.terminator,
            }
            smCommsMsg = f"ERROR: Can't talk to sourcemeter\nTried Ethernet<-->Serial link via {host}:{port}\nThe sourcemeter's comms parameters must match the Ethernet<-->Serial adapter's parameters\nand the terminator should be configured as <CR>"
        else:
            smCommsMsg = "ERROR: Can't talk to sourcemeter"
            openParams = {"resource_name": self.addressString}

        sm = rm.open_resource(**openParams)

        if sm.interface_type == visa.constants.InterfaceType.gpib:
            if os.name != "nt":
                sm.send_ifc()
            sm.clear()
            sm._read_termination = "\n"

        try:
            sm.write("*RST")
            sm.write(":status:preset")
            sm.write(":system:preset")
            # ask the device to identify its self
            self.idn = sm.query("*IDN?")
        except:
            print('Unable perform "*IDN?" query.')
            exctype, value = sys.exc_info()[:2]
            print(value)
            # try:
            #  sm.close()
            # except:
            #  pass
            print(smCommsMsg)
            raise ValueError("Failed to talk to sourcemeter.")

        if self.idnContains in self.idn:
            if not self.quiet:
                print("Sourcemeter found:")
                print(self.idn)
        else:
            raise ValueError("Got a bad response to *IDN?: {:s}".format(self.idn))

        return sm

    def _setupSourcemeter(self, twoWire, front):
        """ Do initial setup for sourcemeter
    """
        sm = self.sm
        sm.timeout = 50000  # long enough to collect an entire sweep [ms]

        sm.write(":status:preset")
        sm.write(":system:preset")
        sm.write(":trace:clear")
        sm.write(":output:smode himpedance")

        warnings.filterwarnings("ignore")
        if sm.interface_type == visa.constants.InterfaceType.asrl:
            self.dataFormat = "ascii"
            sm.values_format.use_ascii("f", ",")
        elif sm.interface_type == visa.constants.InterfaceType.gpib:
            self.dataFormat = "sreal"
            sm.values_format.use_binary("f", False, container=np.array)
        else:
            self.dataFormat = "ascii"
            sm.values_format.use_ascii("f", ",")
        warnings.resetwarnings()

        sm.write("format:data {:s}".format(self.dataFormat))

        sm.write("source:clear:auto off")

        self.setWires(twoWire=twoWire)

        sm.write(":sense:function:concurrent on")
        sm.write(':sense:function "current:dc", "voltage:dc"')
        sm.write(":format:elements time,voltage,current,status")

        # use front terminals?
        self.setTerminals(front=front)

        self.src = sm.query(":source:function:mode?")
        sm.write(":system:beeper:state off")
        sm.write(":system:lfrequency:auto on")
        sm.write(":system:time:reset")

        sm.write(":system:azero off")  # we'll do this once before every measurement
        sm.write(":system:azero:caching on")

        # TODO: look into contact checking function of 2400 :system:ccheck

    def disconnect(self):
        """Close VISA resource."""
        self.sm.outOn(False)
        self.sm.close()

    def setWires(self, twoWire=False):
        if twoWire:
            self.sm.write(":system:rsense off")  # four wire mode off
        else:
            self.sm.write(":system:rsense on")  # four wire mode on

    def setTerminals(self, front=False):
        if front:
            self.sm.write(":rout:term front")
        else:
            self.sm.write(":rout:term rear")

    def updateSweepStart(self, startVal):
        self.sm.write(":source:{:s}:start {:.8f}".format(self.src, startVal))

    def updateSweepStop(self, stopVal):
        self.sm.write(":source:{:s}:stop {:.8f}".format(self.src, stopVal))

    def setOutput(self, outVal):
        self.sm.write(":source:{:s} {:.8f}".format(self.src, outVal))

    def write(self, toWrite):
        self.sm.write(toWrite)

    def query_values(self, query):
        if self.dataFormat == "ascii":
            return self.sm.query_ascii_values(query)
        elif self.dataFormat == "sreal":
            return self.sm.query_binary_values(query)
        else:
            raise ValueError("Don't know what values format to use!")

    def outOn(self, on=True):
        if on:
            self.sm.write(":output on")
        else:
            self.sm.write(":output off")

    def setNPLC(self, nplc):
        self.sm.write(":sense:current:nplcycles {:}".format(nplc))
        self.sm.write(":sense:voltage:nplcycles {:}".format(nplc))
        if nplc < 1:
            self.sm.write(":display:digits 5")
        else:
            self.sm.write(":display:digits 7")

    def setStepDelay(self, stepDelay=-1):
        if stepDelay == -1:
            self.sm.write(":source:delay:auto on")  # this just sets delay to 1ms
        else:
            self.sm.write(":source:delay:auto off")
            self.sm.write(":source:delay {:0.6f}".format(stepDelay))

    def setupDC(
        self, sourceVoltage=True, compliance=0.04, setPoint=0, senseRange="f",
    ):
        """setup DC measurement operation
    if senseRange == 'a' the instrument will auto range for both current and voltage measurements
    if senseRange == 'f' then the sense range will follow the compliance setting
    if sourceVoltage == False, we'll have a current source at setPoint amps with max voltage +/- compliance volts
    """
        sm = self.sm
        if sourceVoltage:
            src = "voltage"
            snc = "current"
        else:
            src = "current"
            snc = "voltage"
        self.src = src
        sm.write(":source:function {:s}".format(src))
        sm.write(":source:{:s}:mode fixed".format(src))
        sm.write(":source:{:s} {:.8f}".format(src, setPoint))

        sm.write(":sense:{:s}:protection {:.8f}".format(snc, compliance))

        if senseRange == "f":
            sm.write(":sense:{:s}:range:auto off".format(snc))
            sm.write(":sense:{:s}:protection:rsynchronize on".format(snc))
        elif senseRange == "a":
            sm.write(":sense:{:s}:range:auto on".format(snc))
        else:
            sm.write(":sense:{:s}:range {:.8f}".format(snc, senseRange))

        # this again is to make sure the sense range gets updated
        sm.write(":sense:{:s}:protection {:.8f}".format(snc, compliance))

        sm.write(":output on")
        sm.write(":trigger:count 1")

        sm.write(":system:azero once")

    def setupSweep(
        self,
        sourceVoltage=True,
        compliance=0.04,
        nPoints=101,
        start=0,
        end=1,
        senseRange="f",
    ):
        """setup for a sweep operation
    if senseRange == 'a' the instrument will auto range for both current and voltage measurements
    if senseRange == 'f' then the sense range will follow the compliance setting
    if stepDelay == -1 then step delay is on auto (1ms)
    """
        sm = self.sm
        if sourceVoltage:
            src = "voltage"
            snc = "current"
        else:
            src = "current"
            snc = "voltage"
        self.src = src
        sm.write(":source:function {:s}".format(src))
        sm.write(":source:{:s} {:0.6f}".format(src, start))

        # seems to do exactly nothing
        # if snc == 'current':
        #  holdoff_delay = 0.005
        #  sm.write(':sense:current:range:holdoff on')
        #  sm.write(':sense:current:range:holdoff {:.6f}'.format(holdoff_delay))
        #  self.opc()  # needed to prevent input buffer overrun with serial comms (should be taken care of by flowcontrol!)

        sm.write(":sense:{:s}:protection {:.8f}".format(snc, compliance))

        if senseRange == "f":
            sm.write(":sense:{:s}:range:auto off".format(snc))
            sm.write(":sense:{:s}:protection:rsynchronize on".format(snc))
        elif senseRange == "a":
            sm.write(":sense:{:s}:range:auto on".format(snc))
        else:
            sm.write(":sense:{:s}:range {:.8f}".format(snc, senseRange))

        # this again is to make sure the sense range gets updated
        sm.write(":sense:{:s}:protection {:.8f}".format(snc, compliance))

        sm.write(":output on")
        sm.write(":source:{:s}:mode sweep".format(src))
        sm.write(":source:sweep:spacing linear")
        sm.write(":trigger:count {:d}".format(nPoints))
        sm.write(":source:sweep:points {:d}".format(nPoints))
        sm.write(":source:{:s}:start {:.6f}".format(src, start))
        sm.write(":source:{:s}:stop {:.6f}".format(src, end))
        if sourceVoltage:
            self.dV = abs(float(sm.query(":source:voltage:step?")))
        else:
            self.dI = abs(float(sm.query(":source:current:step?")))
        # sm.write(':source:{:s}:range {:.4f}'.format(src,max(start,end)))
        sm.write(":source:sweep:ranging best")
        # sm.write(':sense:{:s}:range:auto off'.format(snc))

        sm.write(":system:azero once")

    def opc(self):
        """returns when all operations are complete
    """
        opcVAl = self.sm.query("*OPC?")
        return

    def arm(self):
        """arms trigger
    """
        self.sm.write(":init")

    def trigger(self):
        """permorms trigger event
    """
        if self.sm.interface_type == visa.constants.InterfaceType.gpib:
            self.sm.assert_trigger()
        else:
            self.sm.write("*TRG")

    def sendBusCommand(self, command):
        """sends a command over the GPIB bus
    See: https://linux-gpib.sourceforge.io/doc_html/gpib-protocol.html#REFERENCE-COMMAND-BYTES
    """
        if self.sm.interface_type == visa.constants.InterfaceType.gpib:
            self.sm.send_command(command)
            # self.sm.send_command(0x08) # whole bus trigger
        else:
            print("Bus commands can only be sent over GPIB")

    def measure(self, nPoints=1):
        """Makes a measurement and returns the result
    """
        if self.sm.interface_type == visa.constants.InterfaceType.gpib:
            vals = self.sm.read_binary_values(data_points=nPoints * 4)
        else:
            vals = self.sm.query_ascii_values(":read?")
        return vals

    def measureUntil(
        self,
        t_dwell=np.inf,
        measurements=np.inf,
        cb=lambda x: None,
        handler=None,
        handler_kwargs={},
    ):
        """Meakes measurements until termination conditions are met
    supports a callback after every measurement
    returns a deque of measurements
    """
        i = 0
        t_end = time.time() + t_dwell
        q = deque()
        while (i < measurements) and (time.time() < t_end):
            i = i + 1
            self.setOutput(0)
            measurement = self.measure()
            if handler is not None:
                handler(measurement, **handler_kwargs)
            q.append(measurement)
            cb(measurement)
        return q

    def contact_check(self):
        """Perform contact check.

        Returns
        -------
        failed : bool
            `True` if contact check fails (contact resistance too high). `False` if
            all is well.
        """
        # enable contact check
        self.sm.write(":SYST:CCH ON")

        # set 2 ohm contact resistance
        self.sm.write(":SYST:CCH:RES 2")

        # set 4-wire mode
        self.setWires(twoWire=False)

        # enable contact check pass/fail to be mapped to DIO
        self.sm.write(":CALC2:LIM4:STAT ON")

        # set all DIO to be high on failure
        self.sm.write(":CALC2:LIM4:SOUR2 15")

        # set to resistance measurement function
        self.sm.write(':SENS:FUNC "RES"')

        # enable contact check event detection
        self.sm.write(":TRIG:SEQ2:SOUR CCH")

        # set 2s timeout
        self.sm.write(":TRIG:SEQ2:TOUT 2")

        # turn on output
        self.sm.write(":OUTP ON")

        # trigger check
        self.sm.write(":INIT")

        # query pass/fail
        resp = self.sm.query(":CALC2:LIM4:FAIL?")

        # turn off output
        self.sm.write(":OUTP ON")

        if resp == "0":
            return False
        else:
            # clear failure state
            self.sm.write(":SYST:CLE")
            return True


# testing code
if __name__ == "__main__":
    import pandas as pd

    # connect to our instrument and use the front terminals
    # for testing GPIB connections
    # k = k2400(addressString='GPIB0::24::INSTR', front=True) # gpib address strings expect the thing to be configured for 488.1 comms
    # for testing Ethernet <--> Serial adapter connections, in this case the adapter must be congigured properly via its web interface
    k = k2400(addressString="TCPIP0::10.45.0.186::4000::SOCKET", front=True)

    # setup DC measurement
    forceV = 0
    k.setupDC(setPoint=forceV)

    # this sets up the trigger/reading method we'll use below
    k.write(":arm:source immediate")

    # measure
    mTime = 10
    k.setNPLC(0.01)
    q_dc = k.measureUntil(t_dwell=mTime)

    # create a custom data type to hold our data
    measurement_datatype = np.dtype(
        {
            "names": ["voltage", "current", "time", "status"],
            "formats": ["f", "f", "f", "u4"],
            "titles": ["Voltage [V]", "Current [A]", "Time [s]", "Status bitmask"],
        }
    )

    # convert the data to a numpy array
    qa_dc = np.array([tuple(s) for s in q_dc], dtype=measurement_datatype)
    # print (qa_dc)

    # convert the data to a pandas dataframe and print it
    qf_dc = pd.DataFrame(qa_dc)
    print(f"===== DC V={forceV} for {mTime} seconds =====")
    print(qf_dc.to_string())

    # now for a 101 point voltage sweep from 0 --> 1V
    numPoints = 101
    startV = 0
    endV = 1
    k.setupSweep(
        compliance=0.01, nPoints=numPoints, start=startV, end=endV
    )  # set the sweep up
    q_sw = k.measure(nPoints=numPoints)  # make the measurement

    # convert the result to a numpy array
    qa_sw = np.array(list(zip(*[iter(q_sw)] * 4)), dtype=measurement_datatype)

    # convert the result to a pandas dataframe and print it
    qf_sw = pd.DataFrame(qa_sw)
    print(f"===== {numPoints} point sweep from V={startV} to V={endV} =====")
    print(qf_sw.to_string())

    # shut off the output
    k.outOn(False)
