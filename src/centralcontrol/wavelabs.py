#!/usr/bin/env python3
import socket
import xml.etree.cElementTree as ET
import time
import xml.etree as elT

try:
    from centralcontrol.logstuff import get_logger as getLogger
except:
    from logging import getLogger


class Wavelabs(object):
    """interface for the wavelabs LED solar simulator"""

    idn: str
    iseq = 0  # sequence number for comms with wavelabs software
    def_relay_port = 3335
    def_direct_port = 3334
    spectrum_ms = 1002
    okay_message_codes = [0, -4001]
    retry_codes = [9997, 9998, 9999]  # response codes of these code types should result in a comms retry
    active_recipe = None
    active_intensity = 100
    last_temps = (0.0, 0.0)
    address = None

    class XMLHandler:
        """
        Class for handling the XML responses from the wavelabs software
        """

        def __init__(self):
            self.done_parsing = False
            self.error = None
            self.error_message = ""
            self.run_ID = ""
            self.paramVal = ""
            self.status = ""
            # these are for GetDataSeries[]
            self.this_series = ""
            self.type = []
            self.unit = []
            self.name = []
            self.series = {}

        def start(self, tag, attrib):
            if "iEC" in attrib:
                self.error = int(attrib["iEC"])
            if "sError" in attrib:
                self.error_message = attrib["sError"]
            if "sRunID" in attrib:
                self.run_ID = attrib["sRunID"]
            if "sVal" in attrib:
                self.paramVal = attrib["sVal"]
            if "sStatus" in attrib:
                self.status = attrib["sStatus"]
            if "sName" in attrib:
                self.name.append(attrib["sName"])
            if "sUnit" in attrib:
                self.unit.append(attrib["sUnit"])
            if "sType" in attrib:
                self.type.append(attrib["sType"])
            if tag == "DataSeries":
                self.this_series = attrib["sName"]

        def end(self, tag):
            if tag == "WLRC":
                self.done_parsing = True
            if tag == "DataSeries":
                series = self.series[self.this_series].split(";")
                self.series[self.this_series] = [float(x) for x in series]
                self.this_series = None

        def data(self, data):
            if self.this_series in self.series:
                self.series[self.this_series] = self.series[self.this_series] + data
            else:
                self.series[self.this_series] = data

        def close(self):
            pass

    def __init__(self, kind="wavelabs", address="0.0.0.0:3334", connection_timeout=10, comms_timeout=1, active_recipe=None, intensity=100, **kwargs):
        """
        sets up the wavelabs comms object
        timeouts are in seconds
        """
        self.lg = getLogger(".".join([__name__, type(self).__name__]))  # setup logging

        if "relay" in kind:
            self.relay = True
        else:
            self.relay = False
        self.address = address
        addr_split = address.split(":")
        self.host = addr_split[0]
        if len(addr_split) == 1:
            if self.relay == False:
                self.port = self.def_direct_port
            else:
                self.port = self.def_relay_port
        else:
            self.port = int(addr_split[1])
        self.connection_timeout = connection_timeout
        self.comms_timeout = comms_timeout
        self.sock_file = None
        self.client_socket = None
        self.server_socket = None
        self.active_recipe = active_recipe
        self.active_intensity = int(intensity)

        self.lg.debug("Initialized.")

    def disconnect(self):
        """do our best to clean up and tear down connection"""
        self.lg.debug("Closing wavelabs connection")
        try:
            self.server_socket.settimeout(1)
        except Exception as e:
            pass

        try:
            self.client_socket.settimeout(1)
        except Exception as e:
            pass

        try:
            # craft and issue lower level turn off command with no retries and no ack
            root = ET.Element("WLRC")
            ET.SubElement(root, "CancelRecipe", iSeq=str(self.iseq))
            tree = ET.ElementTree(root)
            tree.write(self.sock_file)
        except Exception as e:
            pass

        try:
            self.client_socket.close()
        except Exception as e:
            pass

        try:
            self.server_socket.close()
        except Exception as e:
            pass

        try:
            self.sock_file.close()
        except Exception as e:
            pass
        self.lg.debug("Wavelabs connection closed.")

    def recvXML(self):
        """reads xml object from socket"""
        target = self.XMLHandler()
        parser = ET.XMLParser(target=target)
        raw_msg = None
        try:
            raw_msg = self.sock_file.readline()
            parser.feed(raw_msg)
        except socket.timeout as to:
            target.error = 9999
            sto = self.client_socket.gettimeout()
            target.error_message = f"Wavelabs comms socket timeout ({sto}s) in recvXML: {to}"
            target.done_parsing = True
        except Exception as e:
            target.error = 9998
            target.error_message = f"General exception: {e}"
            target.done_parsing = True

        if not target.done_parsing:
            target.error = 9997
            target.error_message = "Unable to parse message"

        if target.error not in self.okay_message_codes:
            self.lg.debug(f"Raw message: {raw_msg}")

        try:
            parser.close()
        except Exception as e:
            pass

        return target

    def server_connect(self, timeout=-1):
        """setup a server which listens for the wevelabs software to directly"""
        ret = -3
        address = (self.host, int(self.port))
        self.server_socket = None
        self.client_socket = None
        sto = -99
        if timeout == -1:
            timeout = self.connection_timeout
        try:
            self.server_socket = socket.create_server(address, backlog=0, reuse_port=True)
            self.server_socket.settimeout(timeout)  # set the connection timeout
            sto = self.server_socket.gettimeout()
            (self.client_socket, client_address) = self.server_socket.accept()
            self.server_socket.close()  # there won't be another client
            self.lg.info(f"New direct connection from Wavelabs client software from {client_address}")
            ret = 0
        except socket.timeout as to:
            ret = -1  # timeout waiting for wavelabs software to connect
            self.lg.warning(f"Timeout ({sto}s) waiting for Wavelabs to connect: {to}")
        except Exception as e:
            ret = -2
            self.lg.warning(f"Error while waiting for Wavelabs to connect: {e}")
        return ret

    def relay_connect(self, timeout=-1):
        """forms connection to the relay server"""
        ret = -3
        address = (self.host, int(self.port))
        self.client_socket = None
        sto = -99
        if timeout == -1:
            timeout = self.connection_timeout
        try:
            self.client_socket = socket.create_connection(address, timeout=timeout)
            sto = self.client_socket.gettimeout()
            self.lg.debug(f"New connection to Wavelabs relay via {address}")
            ret = 0
        except socket.timeout as to:
            ret = -1  # timeout waiting for wavelabs software to connect
            self.lg.warning(f"Timeout ({sto}s) connecting to wavelabs relay: {to}")
        except Exception as e:
            ret = -2
            self.lg.warning(f"Error connecting to wavelabs relay: {e}")
        return ret

    def connect(self, timeout=-1, comms_timeout=-1):
        """
        generic connect method, does what's appropriate for getting comms up, timeouts are in seconds
        0 is success
        -1 is timeout
        -2 is general connection error (socket not open after connect)
        -3 is programming/logic error
        """
        ret = -3
        if timeout == -1:
            timeout = self.connection_timeout
        if comms_timeout == -1:
            comms_timeout = self.comms_timeout
        self.iseq = 0
        if self.relay == False:  # for starting a server for a direct connection from Wavelabs software
            ret = self.server_connect(timeout=timeout)
        else:  # relay case
            ret = self.relay_connect(timeout=timeout)
        if ret == 0:
            self.client_socket.settimeout(comms_timeout)
            self.sock_file = self.client_socket.makefile(mode="rwb")
            if self.relay:
                self.idn = "wavelabs-relay"
            else:
                self.idn = "wavelabs"
            if self.active_recipe is not None:
                ret = self.activate_recipe(self.active_recipe)
                if ret == 0:
                    ret = self.set_intensity(self.active_intensity)
                    if ret != 0:
                        self.lg.debug("Failed to set recipe intensity in connect()")
                else:
                    self.lg.debug("Failed to activate recipe in connect()")
        else:
            self.lg.debug("Wavelabs.connect() failed, cleaning up.")
            self.disconnect()
        return ret

    def query(self, root):
        """perform a wavelabs query"""

        n_tries = 3
        for attempt in range(n_tries):
            tree = ET.ElementTree(root)
            try:
                tree.write(self.sock_file)
            except Exception as e:
                self.lg.debug(f"Couldn't write to socket: {e}")
            response = self.recvXML()

            if response.error in self.retry_codes:
                self.lg.debug(response.error_message)
                self.lg.debug(f"doing query() retry number {attempt}...")
                self.disconnect()
                self.connect()
            else:
                break
        else:
            self.lg.warning("Wavelabs comms retry limit exceeded.")

        if response.error not in self.okay_message_codes:
            self.lg.error(f"Got error number {response.error} from WaveLabs software: {response.error_message}")
            raise ValueError(f"Error {response.error}: {response.error_message}")

        return response

    def startFreeFloat(self, time=0, intensity_relative=100, intensity_sensor=0, channel_nums=["8"], channel_values=[50.0]):
        """starts/modifies/ends a free-float run"""
        root = ET.Element("WLRC")
        se = ET.SubElement(root, "StartFreeFloat", iSeq=str(self.iseq), fTime=str(time), fIntensityRelative=str(intensity_relative), fIntensitySensor=str(intensity_sensor))
        self.iseq = self.iseq + 1
        num_chans = len(channel_nums)
        for i in range(num_chans):
            ET.SubElement(se, "Channel", iCh=str(channel_nums[i]), fInt=str(channel_values[i]))
        response = self.query(root)
        if response.error != 0:
            self.lg.debug("Wavelabs FreeFloat command could not be handled")
        return response.error

    def activate_recipe(self, recipe_name=None):
        """activate a solar sim recipe by name"""
        if recipe_name is None:
            recipe_name = self.active_recipe
        if recipe_name is not None:
            root = ET.Element("WLRC")
            ET.SubElement(root, "ActivateRecipe", iSeq=str(self.iseq), sRecipe=recipe_name)
            self.iseq = self.iseq + 1
            response = self.query(root)
            if response.error != 0:
                self.lg.debug(f"Wavelabs recipe {recipe_name} could not be activated, check that it exists")
            else:
                self.active_recipe = recipe_name
            ret = response.error
        else:
            self.lg.debug(f"No recipe given to activate")
            ret = -1
        return ret

    def get_run_status(self):
        """get run status"""
        root = ET.Element("WLRC")
        element_name = "GetRunStatus"
        ET.SubElement(root, element_name, iSeq=str(self.iseq))
        self.iseq = self.iseq + 1
        response = self.query(root)
        if response.error != 0:
            self.lg.debug(f"Trouble with Wavelabs comms with {element_name}")
            ret = response.error
        else:
            ret = response.status
        return ret

    #    def get_info(self):
    #        """get setup info, cmd not documented/implemented by wavelabs"""
    #        root = ET.Element("WLRC")
    #        element_name = "GetInfo"
    #        ET.SubElement(root, element_name, iSeq=str(self.iseq))
    #        self.iseq = self.iseq + 1
    #        response = self.query(root)
    #        if response.error != 0:
    #            self.lg.debug(f"Trouble with Wavelabs comms with {element_name}")
    #            ret = None
    #        else:
    #            ret = response.status
    #        return ret

    def waitForResultAvailable(self, timeout_ms=10000, run_ID=None):
        """
        wait for result from a recipe to be available
        timeout_ms is in ms and should be just longer than you expect the recipe to run for
        """
        root = ET.Element("WLRC")
        if run_ID == None:
            ET.SubElement(root, "WaitForResultAvailable", iSeq=str(self.iseq), fTimeout=str(timeout_ms))
        else:
            ET.SubElement(root, "WaitForResultAvailable", iSeq=str(self.iseq), fTimeout=str(timeout_ms), sRunID=run_ID)
        self.iseq = self.iseq + 1
        old_tout = self.client_socket.gettimeout()
        self.client_socket.settimeout(timeout_ms / 1000 + 1000)
        response = self.query(root)
        self.client_socket.settimeout(old_tout)
        if response.error != 0:
            self.lg.debug("ERROR: Failed to wait for wavelabs result")
        return response.error

    def waitForRunFinished(self, timeout_ms=10000, run_ID=None):
        """
        wait for the current run to finish
        timeout_ms is in ms
        """
        root = ET.Element("WLRC")
        if run_ID == None:
            ET.SubElement(root, "WaitForRunFinished", iSeq=str(self.iseq), fTimeout=str(timeout_ms))
        else:
            ET.SubElement(root, "WaitForRunFinished", iSeq=str(self.iseq), fTimeout=str(timeout_ms), sRunID=run_ID)
        self.iseq = self.iseq + 1
        old_tout = self.client_socket.gettimeout()
        self.client_socket.settimeout(timeout_ms / 1000 + 1000)
        response = self.query(root)
        self.client_socket.settimeout(old_tout)
        if response.error != 0:
            self.lg.debug("Failed to wait for wavelabs run to finish")
        return response.error

    def getRecipeParam(self, recipe_name=None, step=1, device="Light", param="Intensity"):
        if recipe_name is None:
            recipe_name = self.active_recipe
        ret = None
        root = ET.Element("WLRC")
        ET.SubElement(root, "GetRecipeParam", iSeq=str(self.iseq), sRecipe=recipe_name, iStep=str(step), sDevice=device, sParam=param)
        self.iseq = self.iseq + 1
        response = self.query(root)
        if response.error != 0:
            self.lg.debug("Failed to get wavelabs recipe parameter")
            ret = None
        else:
            ret = response.paramVal
        return ret

    def getResult(self, param="totalirradiance_300_1200", run_ID=None):
        ret = None
        root = ET.Element("WLRC")
        reName = "GetResult"
        if run_ID == None:
            ET.SubElement(root, reName, iSeq=str(self.iseq), sParam=param)
        else:
            ET.SubElement(root, reName, iSeq=str(self.iseq), sParam=param, sRunID=run_ID)
        self.iseq = self.iseq + 1
        response = self.query(root)
        if response.error != 0:
            self.lg.debug(f"Failed to getResult from wavelabs. Raw Request: {elT.ElementTree.tostring(root, method='xml')}")
            ret = None
        else:
            ret = response.paramVal
        return ret

    def getDataSeries(self, step=1, device="LE", curve_name="Irradiance-Wavelength", attributes="raw", run_ID=None):
        """returns a data series from SinusGUI"""
        ret = None
        root = ET.Element("WLRC")
        if run_ID == None:
            ET.SubElement(root, "GetDataSeries", iSeq=str(self.iseq), iStep=str(step), sDevice=device, sCurveName=curve_name, sAttributes=attributes)
        else:
            ET.SubElement(root, "GetDataSeries", iSeq=str(self.iseq), iStep=str(step), sDevice=device, sCurveName=curve_name, sAttributes=attributes, sRunID=run_ID)
        self.iseq = self.iseq + 1
        response = self.query(root)
        if response.error != 0:
            self.lg.debug("Failed to getDataSeries")
        else:
            ret = []
            n_series = len(response.name)  # number of data series we got
            for i in range(n_series):
                series = {}
                series["name"] = response.name
                series["unit"] = response.unit
                series["type"] = response.type
                series["data"] = response.series
                ret.append(series)
        return ret

    def setRecipeParam(self, recipe_name=None, step=1, device="Light", param="Intensity", value=100.0):
        if recipe_name is None:
            recipe_name = self.active_recipe
        root = ET.Element("WLRC")
        ET.SubElement(root, "SetRecipeParam", iSeq=str(self.iseq), sRecipe=recipe_name, iStep=str(step), sDevice=device, sParam=param, sVal=str(value))
        self.iseq = self.iseq + 1
        response = self.query(root)
        if response.error != 0:
            self.lg.debug(f"Failed to set recipe parameter. {response=}")
        else:
            self.activate_recipe(recipe_name=recipe_name)
        return response.error

    def on(self):
        """starts the last activated recipe"""
        root = ET.Element("WLRC")
        ET.SubElement(root, "StartRecipe", iSeq=str(self.iseq), sAutomationID="justtext")
        self.iseq = self.iseq + 1
        response = self.query(root)
        if response.error != 0:
            self.lg.debug(f"Failed to StartRecipe. {response=}")
            ret = response.error
        else:
            ret = response.run_ID
        return ret

    def off(self):
        """cancel a currently running recipe"""
        root = ET.Element("WLRC")
        ET.SubElement(root, "CancelRecipe", iSeq=str(self.iseq))
        self.iseq = self.iseq + 1
        response = self.query(root)
        if response.error != 0:
            self.lg.debug(f"Failed to CancelRecipe. {response=}")
        return response.error

    def exitProgram(self):
        """closes the wavelabs solar sim program on the wavelabs PC"""
        root = ET.Element("WLRC")
        ET.SubElement(root, "ExitProgram", iSeq=str(self.iseq))
        self.iseq = self.iseq + 1
        response = self.query(root)
        if response.error != 0:
            self.lg.debug(f"Could not exit WaveLabs program {response=}")
        return response

    def get_runtime(self):
        return self.getRecipeParam(param="Duration")

    def set_runtime(self, duration):
        return self.setRecipeParam(param="Duration", value=int(duration))

    def get_intensity(self):
        return self.getRecipeParam(param="Intensity")

    def set_intensity(self, intensity):
        ret = self.setRecipeParam(param="Intensity", value=int(intensity))
        if ret == 0:
            self.active_intensity = int(intensity)
        return ret

    def get_ir_led_temp(self, run_ID=None):
        str_tmp = self.getResult(param="Temperature_LedBox_IR", run_ID=run_ID)
        return float(str_tmp)

    def get_vis_led_temp(self, run_ID=None):
        str_tmp = self.getResult(param="Temperature_LedBox_Vis", run_ID=run_ID)
        return float(str_tmp)

    def get_temperatures(self):
        """
        returns a list of light engine temperature measurements
        """
        temp = []
        temp.append(self.get_vis_led_temp())
        temp.append(self.get_ir_led_temp())
        self.last_temps = temp
        return temp

    def get_spectrum(self):
        """ "assumes a recipe has been set"""
        x = []
        y = []
        old_duration = None
        try:
            self.off()
            old_duration = self.getRecipeParam(param="Duration")
            if old_duration is not None:
                ret = self.setRecipeParam(param="Duration", value=self.spectrum_ms)
                if ret == 0:
                    run_ID = self.on()
                    if run_ID is not None:
                        ret = self.waitForRunFinished(run_ID=run_ID)
                        if ret == 0:
                            ret = self.waitForResultAvailable(run_ID=run_ID)
                            if ret == 0:
                                spectra = self.getDataSeries(run_ID=run_ID)
                                if spectra is not None:
                                    spectrum = spectra[0]
                                    x = spectrum["data"]["Wavelenght"]
                                    y = spectrum["data"]["Irradiance"]
                                    temp = self.get_temperatures()  # mostly to record it in the logs
                                    self.lg.debug(f"get_spectrum() complete with {temp=}")
        finally:
            if old_duration is not None:
                self.setRecipeParam(param="Duration", value=int(old_duration))
                self.lg.debug(f"Resetting recipe duration to its previous value: {old_duration} [ms]")
        if len(x) == 0:
            raise (ValueError("Unable to fetch spectrum."))
        return (x, y)


if __name__ == "__main__":
    do_disco = False
    gui_plot_spectrum = False

    wl = Wavelabs(address="0.0.0.0:3334", active_recipe="AM1.5G", relay=False)  # for direct connection
    # wl = Wavelabs(host='127.0.0.1', port=3335, relay=True, active_recipe='am1_5_1_sun')  #  for comms via relay
    print("Connecting to light engine...")
    wl.connect()
    status = wl.get_run_status()
    print(f"Light engine status: {status}")
    old_intensity = wl.getRecipeParam(param="Intensity")
    old_duration = wl.getRecipeParam(param="Duration")
    new_intensity = 100.0
    new_duration = 5.001  # in seconds
    if new_duration < 3:
        raise (ValueError("Pick a new duration larger than 3"))
    wl.setRecipeParam(param="Duration", value=new_duration * 1000)
    wl.setRecipeParam(param="Intensity", value=new_intensity)

    duration = wl.getRecipeParam(param="Duration")
    intensity = wl.getRecipeParam(param="Intensity")
    print("Recipe Duration = {:} [s]".format(float(duration) / 1000))
    print("Recipe Intensity = {:} [%]".format(intensity))

    print("Light turns on in...")
    time.sleep(1)
    print("3...")
    time.sleep(1)
    print("2...")
    time.sleep(1)
    print("1...")
    time.sleep(1)
    print("Now!")
    run_ID = wl.on()
    print("Run ID: {:}".format(run_ID))
    print("Light turns off in {:} [s]".format(new_duration))
    time.sleep(new_duration - 3)
    print("3...")
    time.sleep(1)
    print("2...")
    time.sleep(1)
    print("1...")
    time.sleep(1)
    print("Now!")
    wl.waitForRunFinished(run_ID=run_ID)
    wl.waitForResultAvailable(run_ID=run_ID)

    print("Reading LED temps...")
    print(f"Vis LED temp = {wl.get_vis_led_temp(run_ID=run_ID)}")
    print(f"IR LED temp = {wl.get_ir_led_temp(run_ID=run_ID)}")

    print(f"Getting spectrum data...")
    spectra = wl.getDataSeries(run_ID=run_ID)
    spectrum = spectra[0]
    x = spectrum["data"]["Wavelenght"]
    y = spectrum["data"]["Irradiance"]
    print(f"Success! Data length = {len(y)}")

    wl.off()
    wl.activate_recipe()
    wl.setRecipeParam(param="Intensity", value=old_intensity)
    wl.setRecipeParam(param="Duration", value=old_duration)

    duration = wl.getRecipeParam(param="Duration")
    intensity = wl.getRecipeParam(param="Intensity")
    print("Recipe Duration = {:} [s]".format(float(duration) / 1000))
    print("Recipe Intensity = {:} [%]".format(intensity))

    if gui_plot_spectrum == True:
        import matplotlib.pyplot as plt
        import pandas as pd

        plt.plot(x, y)
        plt.ylabel("Irradiance")
        plt.xlabel("Wavelength [nm]")
        plt.grid(True)
        plt.show()

    if do_disco == True:
        print("Now we do the Christo Disco!")
        chan_names = ["all"]
        chan_values = [0.0]
        disco_time = 10000  # [ms]
        wl.startFreeFloat(time=disco_time, channel_nums=chan_names, channel_values=chan_values)
        n_chans = 21
        disco_sleep = disco_time / n_chans
        disco_val = 75
        chan_names = [str(x) for x in range(1, n_chans + 1)]
        for i in range(n_chans):
            print("{:}% on Channel {:}".format(disco_val, chan_names[i]))
            chan_values = [0] * n_chans
            chan_values[i] = disco_val
            wl.startFreeFloat(time=disco_time, channel_nums=chan_names, channel_values=chan_values)
            time.sleep(disco_sleep / 1000)
        wl.startFreeFloat()  # stop freefloat
    wl.disconnect()
    print("Done!")
