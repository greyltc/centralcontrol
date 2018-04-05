#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# written by grey@christoforo.net

import socket # for talking to switch hardware
from toolkit import k2400
import sys
import argparse
import time
import numpy
import mpmath
from scipy import special
parser = argparse.ArgumentParser(description='Automated solar cell IV curve collector using a Keithley 2400 sourcemeter. Data is written to stdout and human readable messages are written to stderr.')

parser.add_argument("address", nargs='?', default=None, type=str, help="VISA resource name for sourcemeter")
parser.add_argument("switch_address", nargs='?', default=None, type=str, help="IP address for switch hardware")
parser.add_argument("pixel_address", nargs='?', default=None, type=str, help="Pixel to scan (A1, A2,...)")
#parser.add_argument("t_dwell", nargs='?', default=None,  type=int, help="Total number of seconds for the dwell phase(s)")
parser.add_argument('--dummy', default=False, action='store_true', help="Run in dummy mode (doesn't need sourcemeter, generates simulated device data)")
parser.add_argument('--visa_lib', type=str, default='@py', help="Path to visa library in case pyvisa can't find it, try C:\\Windows\\system32\\visa64.dll")
parser.add_argument('--file', type=str, help="Write output data stream to this file in addition to stdout.")
parser.add_argument("--scan", default=False, action='store_true', help="Scan for obvious VISA resource names, print them and exit")
parser.add_argument("--front", default=False, action='store_true', help="Use the front terminals")
parser.add_argument("--two-wire", default=False, dest='twoWire', action='store_true', help="Use two wire mode")
parser.add_argument("--terminator", type=str, default='0A', help="Instrument comms read & write terminator (enter in hex)")
parser.add_argument("--baud", type=int, default=57600, help="Instrument comms baud rate")
parser.add_argument("--port", type=int, default=23, help="Port to connect to switch hardware")
parser.add_argument('--xmas-lights', default=False, action='store_true', help="Connectivity test. Probs only run this with commercial LEDs.")
parser.add_argument('--snaith', default=False, action='store_true', help="Run the IV scan from Isc --> Voc")
parser.add_argument('--area', type=float, default=1.0, help="Specify device area in cm^2")
parser.add_argument('--mppt', type=float, default=0, help="Do maximum power point tracking for this many seconds")

args = parser.parse_args()

args.terminator = bytearray.fromhex(args.terminator).decode()

dataDestinations = [sys.stdout]
smCommsMsg = "ERROR: Can't talk to sourcemeter\nDefault sourcemeter serial comms params are: 57600-8-n with <LF> terminator and xon-xoff flow control."

sm2 = k2400(visa_lib=args.visa_lib)
sm = sm2.sm

if args.scan:
    try:
        rm = visa.ResourceManager('@py')
        pyvisaList = rm.list_resources()
        print ("===pyvisa-py===")
        print (pyvisaList)
    except:
        pass
    try:
        if args.visa_lib is not None:
            rm = visa.ResourceManager(args.visa_lib)
        else:
            rm = visa.ResourceManager()
        niList = rm.list_resources()
        print ('==='+str(rm.visalib)+'===')
        print (niList)
    except:
        pass
    sys.exit(0)
else: # not scanning
    if (args.address is None) or (args.switch_address is None) or (args.pixel_address is None):
        parser.error("the following arguments are required: address, switch_address, pixel_address (unless you use --scan)")

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((args.switch_address, args.port))
s.settimeout(0.5)
sf = s.makefile("rwb", buffering=0)

def myPrint(*args,**kwargs):
    if kwargs.__contains__('file'):
        print(*args,**kwargs) # if we specify a file dest, don't overwrite it
    else:# if we were writing to stdout, also write to the other destinations
        for dest in dataDestinations:
            kwargs['file'] = dest
            print(*args,**kwargs)

def pix_picker(sockFile, substrate, pixel):
    win = False
    try:
        cmd = "s" + substrate + str(pixel) + "\r"
        sockFile.write(cmd.encode())
        sockFile.flush()
        win = getResponse()
    except:
        pass
    
    if not win:
        print("WARNING: unable to set pixel with command, {:s}".format(cmd))

    return win

def weAreDone(sm):
    sm.write('*RST')
    sm.close()
    if args.file is not None:
        f.close()    
    myPrint("Finished with no errors.", file=sys.stderr, flush=True)
    sys.exit(0) # TODO: should check all the status values and immediately exit -3 if something is not right

def getResponse():
    win = False
    try:
        line = sf.readline()
        first3 = sf.read(3)
        if first3.decode() == ">>>":
            win = True
    except:
        pass
    return win

sf.write(b"\r") # check on switch
sf.flush()
substrate = args.pixel_address[0]
if not (getResponse() and pix_picker(sf,substrate,0)):
    myPrint("Got bad response from switch. Exiting now.", file=sys.stderr, flush=True)
    sys.exit(-1)

if args.file is not None:
    f = open(args.file, 'w')
    dataDestinations.append(f)

if not args.dummy:
    #timeoutMS = 50000
    timeoutMS = 300 # initial comms timeout
    if 'ASRL' in args.address:
        openParams = {'resource_name': args.address, 'timeout': timeoutMS, 'read_termination': args.terminator,'write_termination': args.terminator, 'baud_rate': args.baud, 'flow_control':visa.constants.VI_ASRL_FLOW_XON_XOFF}
    elif 'GPIB' in args.address:
        openParams = {'resource_name': args.address, 'write_termination': args.terminator}# , 'io_protocol': visa.constants.VI_HS488
        addrParts = args.address.split('::')
        board = addrParts[0][4:]
        address = addrParts[1]
        smCommsMsg = "ERROR: Can't talk to sourcemeter\nIs GPIB controller {:} correct?\nIs the sourcemeter configured to listen on address {:}?".format(board,address)
    else:
        openParams = {'resource_name': args.address}
    
    myPrint("Connecting to", openParams['resource_name'], "...", file=sys.stderr, flush=True)
    connectedVia = None
    try:
        rm = visa.ResourceManager('@py') # first try native python pyvisa-py backend
        sm = rm.open_resource(**openParams)
        connectedVia = 'pyvisa-py'
    except:
        exctype, value1 = sys.exc_info()[:2]
        try:
            if args.visa_lib is not None:
                rm = visa.ResourceManager(args.visa_lib)
            else:
                rm = visa.ResourceManager()
            sm = rm.open_resource(**openParams)
            connectedVia = 'pyvisa-default'
        except:
            exctype, value2 = sys.exc_info()[:2]
            myPrint('Unable to connect to instrument.', file=sys.stderr, flush=True)
            myPrint('Error 1 (using pyvisa-py backend):', file=sys.stderr, flush=True)
            myPrint(value1, file=sys.stderr, flush=True)
            myPrint('Error 2 (using pyvisa default backend):', file=sys.stderr, flush=True)
            myPrint(value2, file=sys.stderr, flush=True)
            try:
                sm.close()
            except:
                pass
            print(smCommsMsg)
            sys.exit(-1)
    myPrint("Connection established via {:}".format(connectedVia), file=sys.stderr, flush=True)
    myPrint("Querying device type...", file=sys.stderr, flush=True)
    try:
        if hasattr(sm,'bytes_in_buffer') and (sm.bytes_in_buffer > 0):
            junk = sm.read_raw(size = sm.bytes_in_buffer)
        
        time.sleep(0.1)
        if 'GPIB' in args.address:
            sm.send_ifc()
            sm.clear()
            sm._read_termination = '\n'
        sm.write('*RST')
        # ask the device to identify its self
        idnString = sm.query("*IDN?")
    except:
        myPrint('Unable perform "*IDN?" query.', file=sys.stderr, flush=True)
        exctype, value = sys.exc_info()[:2]
        myPrint(value, file=sys.stderr, flush=True)
        try:
            sm.close()
        except:
            pass
        print(smCommsMsg)
        sys.exit(-2)
    if 'KEITHLEY' in idnString:
        myPrint("Sourcemeter found:", file=sys.stderr, flush=True)
        myPrint(idnString, file=sys.stderr, flush=True)
        sm.timeout = 50000 #long enough to collect an entire sweep
        if 'ASRL' in args.address:
            sm.values_format.is_binary = False
            formatCmd = "format:data ascii"
        elif 'GPIB' in args.address:
            formatCmd = "format:data sreal"
            sm.values_format.datatype = 'f'
        else:
            sm.values_format.is_binary = False
            formatCmd = "format:data ascii"
            
    else:
        print(smCommsMsg)
        sys.exit(-3)
else: # dummy mode
    class deviceSimulator():
        def __init__(self):
            myPrint("Dummy mode initiated...", file=sys.stderr, flush=True)
            self.t0 = time.time()
            self.measurementTime = 0.01 # [s] the time it takes the simulated sourcemeter to make a measurement
            
            self.Rs = 9.28 #[ohm]
            self.Rsh = 1e6 #[ohm]
            self.n = 3.58
            self.I0 = 260.4e-9#[A]
            self.Iph = 6.293e-3#[A]
            self.cellTemp = 29 #degC
            self.T = 273.15 + self.cellTemp #cell temp in K
            self.K = 1.3806488e-23 #boltzman constant
            self.q = 1.60217657e-19 #electron charge
            self.Vth = mpmath.mpf(self.K*self.T/self.q) #thermal voltage ~26mv
            self.V = 0 # voltage across device
            self.I = None# current through device
            self.updateCurrent()
            
            # for sweeps:
            self.sweepMode = False
            self.nPoints = 1001
            self.sweepStart = 1
            self.sweepEnd = 0
            
            self.status = 0
        
        # the device is open circuit
        def openCircuitEvent(self):
            self.I = 0
            Rs = self.Rs
            Rsh = self.Rsh
            n = self.n
            I0 = self.I0
            Iph = self.Iph
            Vth = self.Vth
            Voc = I0*Rsh + Iph*Rsh - Vth*n*mpmath.lambertw(I0*Rsh*mpmath.exp(Rsh*(I0 + Iph)/(Vth*n))/(Vth*n))
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
            I = (Rs*(I0*Rsh + Iph*Rsh - V) - Vth*n*(Rs + Rsh)*mpmath.lambertw(I0*Rs*Rsh*mpmath.exp((Rs*(I0*Rsh + Iph*Rsh - V)/(Rs + Rsh) + V)/(Vth*n))/(Vth*n*(Rs + Rsh))))/(Rs*(Rs + Rsh))
            self.I = float(numpy.real_if_close(numpy.complex(I)))
    
        def write (self, command):
            if command == ":source:current 0":
                self.openCircuitEvent()
            elif command == ":source:voltage:mode sweep":
                self.sweepMode = True
            elif command == ":source:voltage:mode fixed":
                self.sweepMode = False            
            elif ":source:sweep:points " in command:
                self.nPoints = int(command.split(' ')[1])
            elif ":source:voltage:start " in command:
                self.sweepStart = float(command.split(' ')[1])
            elif ":source:voltage:stop " in command:
                self.sweepEnd = float(command.split(' ')[1])
            elif ":source:voltage " in command:
                self.V = float(command.split(' ')[1])
                self.updateCurrent()
            
        def query_values(self, command):
            if command == "READ?":
                if self.sweepMode:
                    sweepArray = numpy.array([],dtype=numpy.float_).reshape(0,4)
                    voltages = numpy.linspace(self.sweepStart,self.sweepEnd,self.nPoints)
                    for i in range(len(voltages)):
                        self.V = voltages[i]
                        self.updateCurrent()
                        time.sleep(self.measurementTime)
                        measurementLine = numpy.array([self.V, self.I, time.time()-self.t0, self.status])
                        sweepArray = numpy.vstack([sweepArray,measurementLine])
                    return sweepArray
                else: # non sweep mode
                    time.sleep(self.measurementTime)
                    measurementLine = numpy.array([self.V, self.I, time.time()-self.t0, self.status])                    
                    return measurementLine
            elif command == ":source:voltage:step?":
                dV = (self.sweepEnd - self.sweepStart)/self.nPoints
                return numpy.array([dV])
        def close(self):
            pass 
    
    sm = deviceSimulator()
    # override functions
    #sm.write = dummy.write
    #sm.query_ascii_values = dummy.query_ascii_values
    #sm.close = doNothing

# sm is now set up (either in dummy or real hardware mode)

def sweep(sm,  sweepParams, voltage=True,):
    if voltage:
        src = 'voltage'
        snc = 'current'
    else:
        src = 'current'
        snc = 'voltage'
    sm.write(':source:{:s} {:0.4f}'.format(src,sweepParams['sweepStart']))
    sm.write(':source:function {:s}'.format(src))
    sm.write(':output on')
    sm.write(':source:{:s}:mode sweep'.format(src))
    sm.write(':source:sweep:spacing linear')
    if sweepParams['stepDelay'] == -1:
        sm.write(':source:delay:auto on') # this just sets delay to 1ms
    else:
        sm.write(':source:delay:auto off')
        sm.write(':source:delay {:0.3f}'.format(sweepParams['stepDelay']))
    sm.write(':trigger:count {:d}'.format(int(sweepParams['nPoints'])))
    sm.write(':source:sweep:points {:d}'.format(int(sweepParams['nPoints'])))
    sm.write(':source:{:s}:start {:.4f}'.format(src,sweepParams['sweepStart']))
    sm.write(':source:{:s}:stop {:.4f}'.format(src,sweepParams['sweepEnd']))
    #sm.write(':source:{:s}:range {:.4f}'.format(src,max(sweepParams['sweepStart'],sweepParams['sweepStart'])))
    sm.write(':source:sweep:ranging best')
    sm.write(':sense:{:s}:protection {:.6f}'.format(snc,sweepParams['compliance']))
    sm.write(':sense:{:s}:range {:.6f}'.format(snc,sweepParams['compliance']))
    sm.write(':sense:current:nplcycles {:}'.format(sweepParams['nplc']))
    sm.write(':sense:voltage:nplcycles {:}'.format(sweepParams['nplc']))
    sm.write(':display:digits 5')
    #sm.write(':display:enable off')
    
    sm.query('*OPC?')

#sm.write('*RST')
sm.write(':status:preset')
sm.write(':system:preset')
sm.write(':trace:clear')
sm.write(':output:smode himpedance')
sm.write(formatCmd)

sm.write('source:clear:auto off')

sm.write(':system:azero on')
if args.twoWire:
    sm.write(':system:rsense off') # four wire mode off
else:
    sm.write(':system:rsense on') # four wire mode on
sm.write(':sense:function:concurrent on')
sm.write(':sense:function "current:dc", "voltage:dc"')

sm.write(':format:elements time,voltage,current,status')

# use front terminals?
if args.front:
    sm.write(':rout:term front')
else:
    sm.write(':rout:term rear')
    
if args.xmas_lights:
    myPrint("LED test mode active", file=sys.stderr, flush=True)
    
    sweepHigh = 0.01 # amps
    sweepLow = 0 # amps    
    
    sweepParams = {} # here we'll store the parameters that define our sweep
    sweepParams['compliance'] = 2.5 # volts
    sweepParams['nPoints'] = 101
    sweepParams['stepDelay'] = -1 # seconds (-1 for auto, nearly zero, delay)
    sweepParams['nplc'] = 0.01
    sweepParams['sweepStart'] = sweepLow
    sweepParams['sweepEnd'] = sweepHigh
    sweepVoltage = False
    
    if sweepVoltage:
        sweepee = 'voltage'
    else:
        sweepee = 'current'
    
    sweep(sm, sweepParams, voltage=sweepVoltage)
    
    substrate = args.pixel_address[0]
    for pix in range(8):
        pix_picker(sf,substrate,pix+1)
        
        sm.write(':source:{:s}:start {:.4f}'.format(sweepee, sweepLow))
        sm.write(':source:{:s}:stop {:.4f}'.format(sweepee, sweepHigh))                
        sm.write(':init')
        sm.query_values(':sense:data:latest?')
        #sm.query_values('FETCH?')

        sm.write(':source:{:s}:start {:.4f}'.format(sweepee, sweepHigh))
        sm.write(':source:{:s}:stop {:.4f}'.format(sweepee, sweepLow)) 
        sm.write(':init')
        sm.query_values(':sense:data:latest?')
        
        # off during switchover
        sm.write(':source:{:s} 0'.format(sweepee))
    
    # deselect all pixels
    pix_picker(sf, substrate, 0)
  
    sm.write(':output off')
        
    
else: # not running in LED test mode
    # let's find our open circuit voltage
    sm.write(':source:function current')
    sm.write(':source:current:mode fixed')
    sm.write(':source:current:range min')
    sm.write(':source:current 0')
    sm.write(':sense:voltage:protection 2')
    sm.write(':sense:voltage:range 2')
    
    sm.write(':sense:voltage:nplcycles 10')
    sm.write(':sense:current:nplcycles 10')
    sm.write(':display:digits 7')
    
    substrate = args.pixel_address[0]
    pix = args.pixel_address[1]
    if not pix_picker(sf, substrate, pix):
        myPrint("ERROR: Pixel selection failure.", file=sys.stderr, flush=True)
        sys.exit(-1)
    
    sm.write(':output on')
    exploring = 1
    myPrint("Measuring Voc...", file=sys.stderr, flush=True)
    [Voc, Ioc, t0, status] = sm.query_values('READ?')
    myPrint(Voc, file=sys.stderr, flush=True)
    
    vOC_measure_time = 10; #[s]
    t = 0
    while t < vOC_measure_time:
        # read OCV
        [Voc, Ioc, now, status] = sm.query_values('READ?')
        myPrint(Voc, file=sys.stderr, flush=True)
        t = now - t0
    
    #sm.write(':output off')
    myPrint('#exploring,time,voltage,current', file=sys.stderr, flush=True)
    
    # derive connection polarity from Voc
    if Voc < 0:
        polarity = -1
    else:
        polarity = 1
    
    myPrint('# i-v file format v1', flush=True)
    myPrint('# Area = {:}'.format(args.area))
    myPrint('# exploring,time,voltage,current', flush=True)
    myPrint('{:1d},{:.4e},{:.4e},{:.4e}'.format(exploring,0,Voc*polarity,Ioc*polarity), flush=True)
    
    # for initial sweep
    ##NOTE: what if Isc degrades the device? maybe I should only sweep backwards
    ##until the power output starts dropping instead of going all the way to zero volts...
    sweepParams = {} # here we'll store the parameters that define our sweep
    sweepParams['maxCurrent'] = 0.04 # amps
    sweepParams['sweepStart'] = Voc # volts
    sweepParams['sweepEnd'] = 0 # volts
    sweepParams['nPoints'] = 1001
    sweepParams['stepDelay'] = -1 # seconds (-1 for auto, nearly zero, delay)
    sweepParams['nplc'] = 0.5
    
    sm.write(':source:voltage {0:0.4f}'.format(sweepParams['sweepStart']))
    sm.write(':source:function voltage')
    sm.write(':output on')    
    sm.write(':source:voltage:mode sweep')
    sm.write(':source:sweep:spacing linear')
    if sweepParams['stepDelay'] == -1:
        sm.write(':source:delay:auto on') # this just sets delay to 1ms
    else:
        sm.write(':source:delay:auto off')
        sm.write(':source:delay {0:0.3f}'.format(sweepParams['stepDelay']))
    sm.write(':trigger:count {0:d}'.format(int(sweepParams['nPoints'])))
    sm.write(':source:sweep:points {0:d}'.format(int(sweepParams['nPoints'])))
    sm.write(':source:voltage:start {0:.4f}'.format(sweepParams['sweepStart']))
    sm.write(':source:voltage:stop {0:.4f}'.format(sweepParams['sweepEnd']))
    dV = sm.query_ascii_values(':source:voltage:step?')[0]
    
    #sm.write(':source:voltage:range {0:.4f}'.format(sweepParams['sweepStart']))
    sm.write(':source:sweep:ranging best')
    sm.write(':sense:current:protection {0:.6f}'.format(sweepParams['maxCurrent']))
    sm.write(':sense:current:range {0:.6f}'.format(sweepParams['maxCurrent']))
    sm.write(':sense:voltage:nplcycles {:}'.format(sweepParams['nplc']))
    sm.write(':sense:current:nplcycles {:}'.format(sweepParams['nplc']))
    sm.write(':display:digits 5')
    
    myPrint("Doing initial exploratory sweep...", file=sys.stderr, flush=True)
    sweepValues = sm.query_values('READ?')
    
    # deselect all pixels
    pix_picker(sf, substrate, 0)
    
    sf.close()
    try:
        s.shutdown(socket.SHUT_RDWR)
    except:
        pass
    s.close()
    
    myPrint("Exploratory sweep done!", file=sys.stderr, flush=True)
    
    sweepValues = numpy.reshape(sweepValues, (-1,4))
    v = sweepValues[:,0]
    i = sweepValues[:,1]
    t = sweepValues[:,2] - t0
    #p = v*i
    #Isc = i[-1]
    # derive new current limit from short circuit current
    #sm.write(':sense:current:range {0:.6f}'.format(Isc*1.2))
    
    # display initial sweep result
    for x in range(len(sweepValues)):
        myPrint('{:1d},{:.4e},{:.4e},{:.4e}'.format(exploring,t[x],v[x]*polarity,i[x]*polarity), flush=True)

