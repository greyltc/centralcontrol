"""Client for running the CLI based on MQTT messages."""

import collections
import json
import multiprocessing as mp
import signal
import warnings

import paho.mqtt.client as mqtt
import psutil

import central_control.cli


# create dummy process
process = mp.Process()

# create dummy config
config = {}


class ContextMQTT(mqtt.Client):
    """MQTT client with context manager methods."""

    def __init__(self):
        """Construct object."""
        super().__init__()

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Exit the runtime context related to this object.

        Make sure everything gets cleaned up properly.
        """
        self.loop_stop()
        self.disconnect()


def _save_config(self, msg):
    """Save config string to cached file so CLI can use it.

    Parameters
    ----------
    msg : str
        Configuration file as a string.
    """
    self.config_file_path = self.cli.cache.joinpath(self.cli.config_file)
    with open(self.config_file_path, "w") as f:
        f.wrtie(msg)

example_config_file_name = "example_config.ini"
config_file_name = "measurement_config.ini"
self.config_file = pathlib.Path(config_file_name)

# let's figure out where the configuration file is
config_env_var = "MEASUREMENT_CONFIGURATION_FILE_NAME"
if config_env_var in os.environ:
    env_config = pathlib.Path(os.environ.get(config_env_var))
else:
    env_config = pathlib.Path()
home_config = pathlib.Path.home() / config_file_name
local_config = pathlib.Path(config_file_name)
example_config = pathlib.Path(example_config_file_name)
example_python_config = pathlib.Path("python") / example_config_file_name
file_sibling_config = self.this_file.parent / example_config_file_name
self.config_warn = False
if self.cl_config.is_file():  # priority 1: check the command line
    lg.debug("Using config file from command line")
    self.config_file = self.cl_config
elif env_config.is_file():  # priority 2: check the environment
    lg.debug(f"Using config file from {config_env_var} variable")
    self.config_file = env_config
elif local_config.is_file():  # priority 3: check in the current drectory
    #lg.debug(f"Using local config file {local_config.resolve()}")
    self.config_file = local_config
elif home_config.is_file():  # priority 4: check in home dir
    lg.debug(
        f"Using config file {config_file_name} in home dir: {pathlib.Path.home()}"
    )
    self.config_file = home_config
elif example_config.is_file():  # priority 5: check in cwd for example
    lg.debug(f"Using example config file: {example_config.resolve()}")
    self.config_file = example_config
    self.config_warn = True
elif example_python_config.is_file():  # priority 6: check in python/for example
    lg.debug(f"Using example config file: {example_python_config.resolve()}")
    self.config_file = example_python_config
    self.config_warn = True
elif file_sibling_config.is_file():
    lg.debug(f"Using example config file next to __file__: {file_sibling_config.resolve()}")
    self.config_file = file_sibling_config
    self.config_warn = True
else:  # and give up
    lg.error("Unable to find a configuration file to load.")
    raise (ValueError("No config file"))

lg.info(f"Using configuration file: {self.config_file.resolve()}")
if self.config_warn == True:
    lg.warning(f"You're running with the example configuration file. That's likely not what you want.")

try:
    with open(self.config_file, "r") as f:
        for line in f:
            lg.debug(line.rstrip())
    self.config.read(str(self.config_file))
except:
    lg.error("Unexpected error parsing config file.")
    lg.error(sys.exc_info()[0])
    raise


def _format_run_msg(self, msg):
    """Convert run msg from GUI to CLI list for subprocess.

    Parameters
    ----------
    msg : dict
        Dictionary of settings sent from the GUI.

    Returns
    -------
    args : list
        List of command line arguments to parse to CLI.
    """
    # TODO: format run msg dict into args
    args = msg

    return args

def _run(self, msg):
    """Run an experiment.

    Parameters
    ----------
    msg : dict
        Dictionary of settings sent from the GUI.
    """
    args = self._format_run_msg(msg)
    self._start_subprocess(args)

def _stop(self):
    """Terminate a subprocess."""
    # check if a process may still be running
    if self.proc is not None:
        try:
            if self.os_name == "posix":
                # posix systems have a keyboard interrupt signal that allows the
                # subprocess to be cleaned up gracefully if the running function
                # is in a context manager.
                self.proc.send_signal(signal.SIGINT)
            else:
                # no graceful way to interrupt a subprocess on windows so this
                # kills it without cleanup.
                # not sure what happens on other os's so this method is probably
                # least likely to have undesirable side-effects.
                self.proc.terminate()
        except ProcessLookupError:
            # process was run but has now finished
            pass
        self.proc = None
    else:
        pass

def _format_cal_eqe_msg(self, msg):
    """Convert calibrate EQE msg from GUI to CLI list for subprocess.

    Parameters
    ----------
    msg : dict
        Dictionary of settings sent from the GUI.

    Returns
    -------
    args : list
        List of command line arguments to parse to CLI.
    """
    # TODO: format run msg dict into args
    args = msg

    return args

def _cal_eqe(self, msg):
    """Measure the EQE reference photodiode."""
    args = self._format_cal_eqe_msg(msg)
    self._start_subprocess(args)

def _format_cal_psu_msg(self, msg):
    """Convert calibrate PSU msg from GUI to CLI list for subprocess.

    Parameters
    ----------
    msg : dict
        Dictionary of settings sent from the GUI.

    Returns
    -------
    args : list
        List of command line arguments to parse to CLI.
    """
    # TODO: format run msg dict into args
    args = msg

    return args

def _cal_psu(self, msg):
    """Measure the reference photodiode as a funtcion of LED current."""
    args = self._format_cal_psu_msg(msg)
    self._start_subprocess(args)

def _home(self):
    """Home the stage."""
    args = ["--home"]
    self._start_subprocess(args)

def _goto(self, msg):
    """Go to a stage position."""
    args = ["--goto"] + msg
    self._start_subprocess(args)

def _read_stage(self):
    """Read the stage position."""
    args = ["--read-stage"]
    self._start_subprocess(args)


def _update_save_settings(self, folder, archive):
    """Tell saver MQTT client where to save and backup data.

    Parameters
    ----------
    folder : str
        Experiment folder name.
    archive : str
        Network address used to back up data files when an experiment completes.
    """
    # create absolute folder path for save directory
    abs_folder = pathlib.Path(self.config["paths"]["save_folder"]).joinpath(folder)

    # context manager handles disconnect, no need to add to self.handlers
    with SettingsHandler() as sh:
        sh.connect(self.MQTTHOST)
        # publish to data saver settings topic
        sh.start_q("cli/data/settings")
        sh.update_settings(str(abs_folder), archive)

def _verify_save_client(self):
    """Verify the MQTT client for saving data is running."""
    # TODO: at verification method.
    pass

def _get_axes(self):
    """Look up number of stage axes from config file and init attribute."""
    self.stage_lengths = [int(x) for x in self.config["stage"]["length"].split(",")]
    # get number of stage axes
    self.axes = len(self.stage_lengths)

def _get_substrate_positions(self, experiment):
    """Calculate absolute positions of all substrate centres.

    Read in info from config file.

    Parameters
    ----------
    experiment : str
        Name used to look up the experiment centre stage position from the config
        file.

    Returns
    -------
    substrate_centres : list of lists
        Absolute substrate centre co-ordinates. Each sublist contains the positions
        along each axis.
    """
    experiment_centre = [
        int(x) for x in self.config["experiment_positions"][experiment].split(",")
    ]

    # read in number substrates in the array along each axis
    self.substrate_number = [
        int(x) for x in self.config["substrates"]["number"].split(",")
    ]

    # get number of substrate centres between the centre and the edge of the
    # substrate array along each axis, e.g. if there are 4 rows, there are 1.5
    # substrate centres to the outermost substrate
    substrate_offsets = []
    substrate_total = 1
    for number in self.substrate_number:
        if number % 2 == 0:
            offset = number / 2 - 0.5
        else:
            offset = np.floor(number / 2)
        substrate_offsets.append(offset)
        substrate_total = substrate_total * number

    self.substrate_total = substrate_total

    # read in substrate spacing in mm along each axis into a list
    substrate_spacing = [
        int(x) for x in self.config["substrates"]["spacing"].split(",")
    ]

    # read in step length in steps/mm
    self.steplength = float(self.config["stage"]["steplength"])

    # get absolute substrate centres along each axis
    axis_pos = []
    for offset, spacing, number, centre in zip(
        substrate_offsets,
        substrate_spacing,
        self.substrate_number,
        experiment_centre,
    ):
        abs_offset = offset * (spacing / self.steplength) + centre
        axis_pos.append(np.linspace(-abs_offset, abs_offset, number))

    # create array of positions
    substrate_centres = list(itertools.product(*axis_pos))

    return substrate_centres

def _build_q(self, pixel_address_string, experiment):
    """Generate a queue of pixels we'll run through.

    Parameters
    ----------
    pixel_address_string : str
        Hexadecimal bitmask string.
    experiment : str
        Name used to look up the experiment centre stage position from the config
        file.

    Returns
    -------
    pixel_q : deque
    """
    # TODO: return support for inferring layout from pcb adapter resistors

    # get substrate centres
    substrate_centres = self._get_substrate_positions(experiment)

    # make sure as many layouts as labels were given
    if ((l1 := len(self.args.layouts)) != self.substrate_total) or (
        (l2 := len(self.args.labels)) != self.substrate_total
    ):
        raise ValueError(
            f"Lists of layouts and labels must match number of substrates in the array: {self.substrate_total}. Layouts list has length {l1} and labels list has length {l2}."
        )

    # create a substrate queue where each element is a dictionary of info about the
    # layout from the config file
    substrate_q = []
    i = 0
    for layout, label, centre in zip(
        self.args.layouts, self.args.labels, substrate_centres
    ):
        # get pcb adapter info from config file
        pcb_name = self.config[layout]["pcb_name"]

        # read in pixel positions from layout in config file
        config_pos = [int(x) for x in self.config[layout]["positions"].split(",")]
        pixel_positions = []
        for i in range(0, len(config_pos), self.axes):
            abs_pixel_position = [
                int(x + y) for x, y in zip(config_pos[i : i + self.axes], centre)
            ]
            pixel_positions.append(abs_pixel_position)

        # find co-ordinate of substrate in the array
        _substrates = np.linspace(1, self.substrate_total, self.substrate_total)
        _array = np.reshape(_substrates, self.substrate_number)
        array_loc = [int(ix) + 1 for ix in np.where(_array == i)]

        substrate_dict = {
            "label": label,
            "array_loc": array_loc,
            "layout": layout,
            "pcb_name": pcb_name,
            "pcb_contact_pads": self.config[pcb_name]["pcb_contact_pads"],
            "pcb_resistor": self.config[pcb_name]["pcb_resistor"],
            "pixels": self.config[layout]["pixels"].split(","),
            "pixel_positions": pixel_positions,
            "areas": self.config[layout]["areas"].split(","),
        }
        substrate_q.append(substrate_dict)

        i += 1

    # TODO: return support for pixel strings that aren't hex bitmasks
    # convert hex bitmask string into bit list where 1's and 0's represent whether
    # a pixel should be measured or not, respectively
    bitmask = [int(x) for x in bin(int(pixel_address_string, 16))[2:]]

    # build pixel queue
    pixel_q = deque()
    for substrate in substrate_q:
        # git bitmask for the substrate pcb
        sub_bitmask = [
            bitmask.pop(-1) for i in range(substrate["pcb_contact_pads"])
        ].reverse()
        # select pixels to measure from layout
        for pixel in substrate["pixels"]:
            if sub_bitmask[pixel - 1] == 1:
                pixel_dict = {
                    "label": substrate["label"],
                    "layout": substrate["layout"],
                    "array_loc": substrate["array_loc"],
                    "pixel": pixel,
                    "position": substrate["pixel_positions"][pixel - 1],
                    "area": substrate["areas"][pixel - 1],
                }
                pixel_q.append(pixel_dict)

    return pixel_q

def _connect_instruments(self):
    """Init fabric object and connect instruments.

    Determine which instruments are connected and their settings from the config
    file.
    """
    if self.args.dummy is False:
        visa_lib = self.config["visa"]["visa_lib"]
        smu_address = self.config["smu"]["address"]
        smu_terminator = self.config["smu"]["terminator"]
        smu_baud = self.config["smu"]["baud"]
        light_address = self.config["solarsim"]["address"]
        controller_address = self.config["controller"]["address"]
        lia_address = self.config["lia"]["address"]
        lia_output_interface = self.config["lia"]["output_interface"]
        mono_address = self.config["mono"]["address"]
        psu_address = self.config["psu"]["address"]
    else:
        visa_lib = None
        smu_address = None
        smu_terminator = None
        smu_baud = None
        light_address = None
        controller_address = None
        lia_address = None
        lia_output_interface = None
        mono_address = None
        psu_address = None

    # connect to insturments
    self.logic.connect(
        dummy=self.args.dummy,
        visa_lib=visa_lib,
        smu_address=smu_address,
        smu_terminator=smu_terminator,
        smu_baud=smu_baud,
        light_address=light_address,
        controller_address=controller_address,
        lia_address=lia_address,
        lia_output_interface=lia_output_interface,
        mono_address=mono_address,
        psu_address=psu_address,
    )

    # set up smu terminals
    self.logic.sm.setTerminals(
        front=self.config.getboolean("smu", "front_terminals")
    )
    self.logic.sm.setWires(twoWire=self.config.getboolean("smu", "two_wire"))

def _disconnect_all(self):
    """Disconnect all MQTT clients."""
    # end all open MQTT clients
    for i in range(len(self.handlers)):
        h = self.handlers.popleft()
        h.end_q()
        h.disconnect()

def _ivt(self):
    """Run through pixel queue of i-v-t measurements."""
    # set the master experiment relay
    self.logic.controller.set_relay("iv")

    # create mqtt data handlers for i-v-t measurements
    # mqtt publisher topics for each handler
    subtopics = []
    subtopics.append(f"cli/data/vt")
    subtopics.append(f"cli/data/iv")
    subtopics.append(f"cli/data/mppt")
    subtopics.append(f"cli/data/it")

    # instantiate handlers
    vdh = DataHandler()
    ivdh = DataHandler()
    mdh = DataHandler()
    cdh = DataHandler()
    handlers = [vdh, ivdh, mdh, cdh]

    # connect handlers to broker and start publisher threads
    for i, dh in enumerate(handlers):
        self.handlers.append(dh)
        dh.connect(self.MQTTHOST)
        dh.start_q(subtopics[i])

    last_label = None
    # scan through the pixels and do the requested measurements
    while len(self.iv_pixel_queue) > 0:
        pixel = self.iv_pixel_queue.popleft()
        label = pixel["label"]
        pix = pixel["pixel"]
        print(f"\nOperating on substrate {label}, pixel {pix}...")

        # add id str to handlers to display on plots
        for dh in handlers:
            dh.idn = f"{label}_pixel{pix}"

        # we have a new substrate
        if last_label != label:
            print(f"New substrate using '{pixel['layout']}' layout!")
            last_label = label

        # move to pixel
        self.logic.goto_stage_position(pixel["position"], handler=self.sh)

        # init parameters derived from steadystate measurements
        ssvoc = None
        ssisc = None

        # get or estimate compliance current
        if type(self.args.current_compliance_override) == float:
            compliance_i = self.args.current_compliance_override
        else:
            # estimate compliance current based on area
            compliance_i = self.logic.compliance_current_guess(pixel["area"])

        # steady state v@constant I measured here - usually Voc
        if self.args.v_t > 0:
            # clear v@constant I plot
            vdh.clear()

            vt = self.logic.steady_state(
                t_dwell=self.args.v_t,
                NPLC=self.args.steadystate_nplc,
                stepDelay=self.args.steadystate_step_delay,
                sourceVoltage=False,
                compliance=self.args.voltage_compliance_override,
                senseRange="a",
                setPoint=self.args.steadystate_i,
                handler=vdh,
            )

            # signal end of measurement
            vdh.end()

            # if this was at Voc, use the last measurement as estimate of Voc
            if self.args.steadystate_i == 0:
                ssvoc = vt[-1]
                self.logic.mppt.Voc = ssvoc

        if (self.args.sweep_1 is True) or (self.args.sweep_1 is True):
            # clear iv plot
            ivdh.clear()

        # TODO: add support for dark measurement, has to use autorange
        if self.args.sweep_1 is True:
            # determine sweep start voltage
            if type(self.args.scan_start_override_1) == float:
                start = self.args.scan_start_override_1
            elif ssvoc is not None:
                start = ssvoc * (
                    1 + (self.config.getfloat("iv", "percent_beyond_voc") / 100)
                )
            else:
                raise ValueError(
                    f"Start voltage wasn't given and couldn't be inferred."
                )

            # determine sweep end voltage
            if type(self.args.scan_end_override_1) == float:
                end = self.args.scan_end_override_1
            else:
                end = (
                    -1
                    * np.sign(ssvoc)
                    * self.config.getfloat("iv", "voltage_beyond_isc")
                )

            print(f"Sweeping voltage from {start} V to {end} V")

            iv1 = self.logic.sweep(
                sourceVoltage=True,
                compliance=compliance_i,
                senseRange="f",
                nPoints=self.args.scan_points,
                stepDelay=self.args.scan_step_delay,
                start=start,
                end=end,
                NPLC=self.args.scan_nplc,
                handler=ivdh,
            )

            Pmax_sweep1, Vmpp1, Impp1, maxIx1 = self.logic.mppt.which_max_power(iv1)

        if self.args.sweep_2 is True:
            # sweep the opposite way to sweep 1
            start = end
            end = start

            print(f"Sweeping voltage from {start} V to {end} V")

            iv2 = self.logic.sweep(
                sourceVoltage=True,
                senseRange="f",
                compliance=compliance_i,
                nPoints=self.args.scan_points,
                start=start,
                end=end,
                NPLC=self.args.scan_nplc,
                handler=ivdh,
            )

            Pmax_sweep2, Vmpp2, Impp2, maxIx2 = self.logic.mppt.which_max_power(iv2)

        if (self.args.sweep_1 is True) or (self.args.sweep_1 is True):
            # signal end of iv measurements
            ivdh.end()

        # TODO: read and interpret parameters for smart mode
        # # determine Vmpp and current compliance for mppt
        # if (self.args.sweep_1 is True) & (self.args.sweep_2 is True):
        #     if abs(Pmax_sweep1) > abs(Pmax_sweep2):
        #         Vmpp = Vmpp1
        #         compliance_i = Impp1 * 5
        #     else:
        #         Vmpp = Vmpp2
        #         compliance_i = Impp2 * 5
        # elif self.args.sweep_1 is True:
        #     Vmpp = Vmpp1
        #     compliance_i = Impp1 * 5
        # else:
        #     # no sweeps have been measured so max power tracker will estimate Vmpp
        #     # based on Voc (or measure it if also no Voc) and will use initial
        #     # compliance set before any measurements were taken.
        #     Vmpp = None
        # self.logic.mppt.Vmpp = Vmpp
        self.logic.mppt.current_compliance = compliance_i

        if self.args.mppt_t > 0:
            print(f"Tracking maximum power point for {self.args.mppt_t} seconds.")

            # clear mppt plot
            mdh.clear()

            # measure voc for 1s to initialise mppt
            vt = self.logic.steady_state(
                t_dwell=1,
                NPLC=self.args.steadystate_nplc,
                stepDelay=self.args.steadystate_step_delay,
                sourceVoltage=False,
                compliance=self.args.voltage_compliance_override,
                senseRange="a",
                setPoint=0,
                handler=mdh,
            )
            self.logic.mppt.Voc = vt[-1]

            mt = self.logic.track_max_power(
                self.args.mppt_t,
                NPLC=self.args.steadystate_nplc,
                stepDelay=self.args.steadystate_step_delay,
                extra=self.args.mppt_params,
                handler=mdh,
            )

            # signal end of measurement
            mdh.end()

        if self.args.i_t > 0:
            # steady state I@constant V measured here - usually Isc
            # clear I@constant V plot
            cdh.clear()
            it = self.logic.steady_state(
                t_dwell=self.args.i_t,
                NPLC=self.args.steadystate_nplc,
                stepDelay=self.args.steadystate_step_delay,
                sourceVoltage=True,
                compliance=compliance_i,
                senseRange="a",
                setPoint=self.args.steadystate_v,
                handler=cdh,
            )

            # signal end of measurement
            cdh.end()

    self.logic.run_done()

def _eqe(self):
    """Run through pixel queue of EQE measurements."""
    self.logic.controller.set_relay("eqe")

    # create mqtt data handler for eqe
    edh = DataHandler()
    self.handlers.append(edh)
    edh.connect(self.MQTTHOST)
    edh.start_q("cli/data/eqe")

    # look up settings from config
    grating_change_wls = [float(x) for x in self.config["monochromator"]["grating_change_wls"].split(",")]
    filter_change_wls = [float(x) for x in self.config["monochromator"]["filter_change_wls"].split(",")]

    while len(self.eqe_pixel_queue) > 0:
        pixel = self.eqe_pixel_queue.popleft()
        label = pixel["label"]
        pix = pixel["pixel"]
        print(f"\nOperating on substrate {label}, pixel {pix}...")

        # add id str to handlers to display on plots
        edh.idn = f"{label}_pixel{pix}"

        # we have a new substrate
        if last_label != label:
            print(f"New substrate using '{pixel['layout']}' layout!")
            last_label = label

        # move to pixel
        self.logic.goto_stage_position(pixel["position"], handler=self.sh)

        print(
            f"Scanning EQE from {self.args.eqe_start_wl} nm to {self.args.eqe_end_wl} nm"
        )

        # clear eqe plot
        # TODO: fill in paths
        edh.clear()
        self.logic.eqe(
            psu_ch1_voltage=self.config.getfloat("psu", "ch1_voltage"),
            psu_ch1_current=self.args.psu_is[0],
            psu_ch2_voltage=self.config.getfloat("psu", "ch2_voltage"),
            psu_ch2_current=self.args.psu_is[1],
            psu_ch3_voltage=self.config.getfloat("psu", "ch3_voltage"),
            psu_ch3_current=self.args.psu_is[2],
            smu_voltage=self.args.eqe_smu_v,
            calibration=False,
            ref_measurement_path=,
            ref_measurement_file_header=1,
            ref_eqe_path=self.cache.joinpath(self.eqe_diode_cal_file),
            ref_spectrum_path=self.cache.joinpath(self.eqe_ref_spectrum_file),
            start_wl=self.args.eqe_start_wl,
            end_wl=self.args.eqe_end_wl,
            num_points=self.args.eqe_num_wls,
            repeats=self.args.eqe_repeats,
            grating_change_wls=grating_change_wls,
            filter_change_wls=filter_change_wls,
            auto_gain=not (self.args.eqe_autogain_off),
            auto_gain_method=self.args.eqe_autogain_method,
            integration_time=self.args.eqe_integration_time,
            handler=edh,
        )

        # signal end of measurement
        edh.end()

def run(self):
    """Act on command line instructions."""
    # get arguments parsed to the command line
    self.args = self._get_args()

    if self.args.repeat is True:
        # retreive args from cached preferences
        self.args = self._load_prefs()
    else:
        # save argparse prefs to cache
        self._save_prefs()

    # load config file and copy ref data to cache
    self._load_config()
    self._cache_ref_data()

    # get mqtt host name from config
    self.MQTTHOST = self.config["network"]["MQTTHOST"]

    # look up number of stage axes from config and init attribute
    self._get_axes()

    # scan for VISA resource names
    if args.scan_visa is True:
        # TODO: add scan get resource names func
        pass

    # create the control logic entity and connect instruments
    self.logic = fabric()
    self._connect_instruments()

    # test hardware
    if args.test_hardware is True:
        # TODO: add hardware test func
        pass

    # verify the save client is available
    self._verify_save_client()

    # tell mqtt data saver where to save
    self._update_save_settings(
        self.args.experiment_folder, self.config["network"]["archive"]
    )

    # create handler for reporting stage position
    self.sh = StageHandler()
    self.handlers.append(self.sh)
    self.sh.connect(self.MQTTHOST)
    self.sh.start_q("cli/stage")

    # home the stage
    if self.args.home is True:
        self.logic.home_stage(self.config["stage"]["length"])

    # goto stage position
    if self.args.goto is not None:
        self.logic.goto_stage_position(self.args.goto, handler=sh)

    # read stage position
    if self.args.read_stage is True:
        self.logic.read_stage_position(handler=sh)

    # round robin contact check
    if self.args.contact_check is True:
        array = self.config["substrates"]["number"].split(",")
        rows = array[0]
        try:
            cols = array[1]
        except IndexError:
            cols = 1
        active_layout = self.config["substrates"]["active_layout"]
        pcb_adapter = self.config[active_layout]["pcb_name"]
        pixels = self.config[pcb_adapter]["pixels"]
        self.logic.check_all_contacts(rows, cols, pixels)

    # calibrate LED PSU if required
    if self.args.calibrate_psu is True:
        pdh = DataHandler()
        self.handlers.append(pdh)
        pdh.connect(self.MQTTHOST)
        pdh.start_q("cli/data/psu")
        pdh.idn = "psu_calibration"
        self.logic.controller.set_relay("eqe")
        # TODO: look up diode location from calibration file
        self.logic.calibrate_psu(
            self.args.calibrate_psu_ch, loc=, handler=pdh
        )

    # measure EQE calibration diode if required
    if self.args.calibrate_eqe is True:
        self.logic.controller.set_relay("eqe")
        # TODO: add calibrate EQE func

    # perform solar sim calibration measurement
    if self.calibrate_solarsim is True:
        # TODO; add calibrate solar sim func
        self.logic.controller.set_relay("iv")

        if self.args.wavelabs_spec_cal_path != "":
            spectrum_cal = np.genfromtxt(
                self.args.wavelabs_spec_cal_path, skip_header=1, delimiter="\t"
            )[:, 1]
        else:
            spectrum_cal = None

        # save spectrum
        if self.logic.spectrum is not None:
            sdh = DataHandler()
            self.handlers.append(sdh)
            sdh.connect(self.MQTTHOST)
            sdh.start_q("cli/data/spectrum")
            sdh.idn = "spectrum"
            sdh.handle_data(self.logic.spectrum)

    # calibration data is saved to cache so copy those files to save directory now
    self._save_cache()

    # build up the queue of pixels to run through
    if self.args.dummy is True:
        self.args.iv_pixel_address = "0x1"
        self.args.eqe_pixel_address = "0x1"

    if self.args.iv_pixel_address is not None:
        self.iv_pixel_queue = self._build_q(
            self.args.iv_pixel_address, experiment="solarsim"
        )
    else:
        self.iv_pixel_queue = []

    if self.args.eqe_pixel_address is not None:
        self.eqe_pixel_queue = self._build_q(
            self.args.eqe_pixel_address, experiment="eqe"
        )
    else:
        self.eqe_pixel_queue = []

    # measure i-v-t
    if len(self.iv_pixel_queue) > 0:
        self._ivt()

    # measure eqe
    if len(self.eqe_pixel_queue) > 0:
        self._eqe()

    # disconnect MQTT handlers
    self._disconnect_all()


def on_message(mqttc, obj, msg):
    """Act on an MQTT message."""
    m = json.loads(msg.payload)
    action = m["action"]
    data = m["data"]

    # perform action depending on which button generated the message
    if action == "get_config":
        _get_config()
    elif action == "set_config":
        _set_config()
    elif action == "run":
        _run(m)
    elif action == "stop":
        _stop()
    elif action == "cal_eqe":
        _cal_eqe(m)
    elif action == "cal_psu":
        _cal_psu(m)
    elif action == "home":
        _home()
    elif action == "goto":
        _goto(m)
    elif action == "read_stage":
        _read_stage()


def _start_process(target, args):
    """Run a function in a new process.

    Parameters
    ----------
    target : function
        Name of function to run in a new process.
    args : tuple
        Arguments to pass to the function formatted as (arg1, arg2, ...,).
    """
    global process

    # try to start a new process. Ignore request if a process is still running.
    if process.is_alive() is False:
        process = mp.Process(target=target, args=args)
        process.start()
        print(f"Started process with PID={process.pid}!")
    else:
        print(f"Cannot start new process. A process is still running with PID={process.pid}.")


def _stop():
    """Stop an active process running a requested function."""
    global process

    # if the process is still alive, stop it
    if process.is_alive() is True:
        process.terminate()
        process.join()
        print(f"Stopped process with PID={process.pid}.")
    else:
        print(f"Process with PID={process.pid} has already stopped.")


def respond(mqttc, kind, data):
    """Publish responses to server/response topic.

    Parameters
    ----------
    mqttc : mqtt.Client
        MQTT client to publish from.
    kind : str
        Name for the kind of data included in the response dictionary.
    data : any jsonifiable type
        Data to be included in the response dictionary.
    """
    payload = json.dumps({"kind": kind, "data": data})
    mqttc.publish("server/response", payload=payload, qos=2).wait_for_publish()


# required when using multiprocessing in windows, advised on other platforms
if __name__ == "__main__":
    import argparse

    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mqtthost",
        default="127.0.0.1",
        help="IP address or hostname of MQTT broker.",
    )
    args = parser.parse_args()

    # try to load the configuration file from the current working directory
    try:
        with open("measurement_config.yaml", "r") as f:
            config = yaml.load(f, Loader=yaml.FullLoader)
    except FileNotFoundError:
        # maybe running a test from project directory
        try:
            with open("example_config.yaml", "r") as f:
                config = yaml.load(f, Loader=yaml.FullLoader)
            print(f"'measurement_config.yaml' not found in current working directory: {os.getcwd()}. Falling back on 'example_config.yaml' project file.")
        except FileNotFoundError:
            raise FileNotFoundError(f"No configuration file could be found in current working directory: {os.getcwd()}. Please run the server from a directory with a valid 'measurement_config.yaml' file.")

    with ContextMQTT() as mqtt_server:
        mqtt_server.on_message = on_message
        # connect MQTT client to broker
        mqtt_server.connect(args.MQTTHOST)
        # subscribe to everything in the server/request topic
        mqtt_server.subscribe("server/request")
        mqtt_server.loop_forever()
