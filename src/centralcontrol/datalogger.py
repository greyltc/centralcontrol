#!/usr/bin/env python3

import time
from pymodbus.client import ModbusTcpClient
from typing import TypedDict

import logging
from centralcontrol.logstuff import get_logger

class DataLogger(ModbusTcpClient):
	"""manages data logging hardware"""
	# TODO: consider adding a lookup table to remap the channel measurements 
	# instead of just the simple scale & offset we have now
	# TODO: switch all this this to async comms when the main program loop switches

	class Channel(TypedDict):
		name: str  # user readable name for channel
		num: int  # hardware channel number
		enabled: bool  # to enable the channel or not
		range: int  # probably use one byte hex strings here to match the docs (start with "0x" eg. "0x08")
		scale: float  # (first) scale the output value by this
		offset: float  # (then) offset the output value by this
		unit: str  # final output value unit string
		delay: float  # number of seconds to pause between samplings (this is not the same as period)
	
	default_channel: Channel = {
		"name": "unset",
		"num": 0,
		"enabled": False,
		"range": 0x08,
		"scale": 0.001,
		"offset": 0.0,
		"unit": "V",
		"delay": 10,
	}

	# all possible analog input range setting hex values
	# taken from https://web.archive.org/web/20250201161740/https://icpdas-europe.com/media/c7/c4/01/1605016552/manual-et7000-et7200.pdf
	# on page 151
	# any given hardware will likely only support some subset of these
	# eg. for which analog input ranges are supported by PET‐7026 hardware, see page 66 of
	# https://web.archive.org/web/20250201162730/https://icpdas-europe.com/media/2f/1a/0e/1605016770/register-table-pet-et7x00.pdf
	# 0x00: -15  to +15   mA
	# 0x01: -50  to +50   mA
	# 0x02: -100 to +100  mV
	# 0x03: -500 to +500  mV
	# 0x04: -1   to +1    V
	# 0x05: -2.5 to +2.5  V
	# 0x06: -20  to +20   mA
	# 0x07: +4   to +20   mA
	# 0x08: -10  to +10   V
	# 0x09: -5   to +5    V
	# 0x0a: -1   to +1    V
	# 0x0b: -500 to +500  mV
	# 0x0c: -150 to +150  mV
	# 0x0d: -20  to +20   mA
	# 0x1a: -0   to +20   mA
	# 0x0e: -210 to +760  degC (Type J TC)
	# 0x0f: -270 to +1372 degC (Type K TC)
	# 0x10: -270 to +400  degC (Type T TC)
	# 0x11: -270 to +1000 degC (Type E TC)
	# 0x12: -0   to +1768 degC (Type R TC)
	# 0x13: -0   to +1768 degC (Type S TC)

	engineering = True  # tell the hardware to return engineering values instead of 2's complement ones (expect anything besides true to break stuff)
	addr_coil_engineering = 631  # aka 00631 (coil base address is 0)
	addr_coil_ai_enable_base = 595  # aka 00595 (coil base address is 0)
	addr_holding_reg_ai_range_base = 427  # aka 40427 (holding register base address is 40000)

	channels: list[Channel] = []

	def __init__(self, *args, **kwargs):
		self.lg = get_logger(".".join([__name__, type(self).__name__]))   # setup logging
		self.lg.debug("DataLogger init starting")

		if kwargs.pop("type") != "icpdas":
			raise RuntimeError("Only icpdas type dataloggers are currently supported")

		if "address" in kwargs:
			args = (kwargs.pop("address"), )  # set the host
		
		if "virtual" in kwargs:
			if kwargs.pop("virtual"):
				self.lg.warning("Virtual dataloggers are not yet supported")  # TODO: support virtual dataloggers
		
		if "enabled" in kwargs:
			if not kwargs.pop("enabled"):
				self.lg.warning("Disabled dataloggers are not yet supported")  # TODO: support disabled dataloggers
		
		if "channels" in kwargs:
			channels = kwargs.pop("channels")
		else:
			channels = []

		for channel in channels:
			this_chan = self.default_channel.copy()
			for key, val in channel.items():
				if key == "name" and "," in val:
					raise RuntimeError("Data logger channel name can not contain ','")
				elif key == "unit" and "," in val:
					raise RuntimeError("Data logger channel unit can not contain ','")
				this_chan[key] = val
			self.channels.append(this_chan)

		return super().__init__(*args, **kwargs)

	def __enter__(self, *args, **kwargs):
		"""connects to the modbus client and then sets up the hardware for measurement"""
		self.lg.debug("DataLogger connecting")
		__client = super().__enter__(*args, **kwargs)
		self.lg.debug(f"DataLogger connected via {self.socket}")

		resp = __client.write_coil(self.addr_coil_engineering, self.engineering)
		if resp.isError():
			self.lg.warning(f"Modbus Exception: {resp}")

		# set up the channels
		for channel in self.channels:
			num = channel["num"]
			enabled = channel["enabled"]
			addr_coil_ai_enable_chan = self.addr_coil_ai_enable_base + channel["num"]
			resp = __client.write_coil(addr_coil_ai_enable_chan, enabled)  # enable/disable the channel
			if resp.isError():
				self.lg.warning(f"Modbus Exception: {resp}")
			
			if enabled:
				addr_holding_reg_ai_range_chan = self.addr_holding_reg_ai_range_base + num
				resp = __client.write_register(addr_holding_reg_ai_range_chan, channel["range"])  # set the analog input channel range
		
		# somehow these writes/setup seems to take ~5 seconds to apply!?
		time.sleep(5)
	
		self.lg.debug("DataLogger hardware setup complete")
		return __client

	def __exit__(self, *args, **kwargs):
		self.lg.debug("DataLogger disconnecting")
		return super().__exit__(*args, **kwargs)
	
	def read_chan(self, chan:Channel) -> float|None:
		if chan in self.channels:
			if not chan["enabled"]:
				self.lg.warning(f"Attempting to read a disabled channel")

			resp = self.read_input_registers(chan["num"])
			if resp.isError():
				self.lg.warning(f"Modbus Exception: {resp}")

			try:
				raw_val = int.from_bytes((resp.registers[0]).to_bytes(2), signed=True)  # handle the sign bit
				ret_val = raw_val * chan["scale"] + chan["offset"]
			except Exception as e:
				self.lg.warning(f"DataLogger handling exception: {e}")
				ret_val = None
		else:
			self.lg.warning(f"Attempt to read unconfigured channel")
			ret_val = None

		return ret_val
	

if __name__ == "__main__":
	import yaml
	import inspect

	# an example config setup
	# NB. current input like this example requires moving a jumper on the hardware from its default position
	cfg_str = """
		datalogger:
		  address: daq
		  virtual: false
		  enabled: true
		  type: icpdas
		  channels:
			- {num: 0, enabled: false}
			- {num: 1, enabled: false}
			- {num: 2, enabled: false}
			- {name: pressure,  num: 3, enabled: true, range: 0x08, scale: 0.001, offset: 0.0, unit: V,  delay: 15.0}
			- {name: intensity, num: 4, enabled: true, range: 0x1a, scale: 0.001, offset: 0.0, unit: mA, delay: 10.0}
			- {num: 5, enabled: false}
		"""

	cfg = yaml.safe_load(inspect.cleandoc(cfg_str))["datalogger"]

	with DataLogger(**cfg) as client:
		for channel in client.channels:
			if channel["enabled"]:
				val = client.read_chan(channel)
				print(f"CH{channel['num']} ({channel['name']}): {val} {channel['unit']}")
