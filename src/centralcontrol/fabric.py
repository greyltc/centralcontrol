#!/usr/bin/env python3

from threading import Event as tEvent
from multiprocessing.synchronize import Event as mEvent

from centralcontrol import virt
from centralcontrol.pcb import Pcb
from centralcontrol.motion import motion


try:
    from centralcontrol.logstuff import get_logger as getLogger
except:
    from logging import getLogger


class Fabric(object):
    """High level experiment control logic"""

    # current_limit = float("inf")
    current_limit = 0.1  # always safe default

    # a virtual pcb object
    fake_pcb = virt.pcb

    # a real pcb object
    real_pcb = Pcb

    # listen to this for kill signals
    killer: tEvent | mEvent

    def __init__(self, killer: tEvent | mEvent = tEvent()):
        """Get software revision."""
        # self.software_revision = __version__
        # print("Software revision: {:s}".format(self.software_revision))
        self.killer = killer

        self.lg = getLogger(".".join([__name__, type(self).__name__]))  # setup logging
        self.lg.debug("Initialized.")

    def __enter__(self):
        """Enter the runtime context related to this object."""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        return False

    def compliance_current_guess(self, area=None, jmax=None, imax=None):
        """Guess what the compliance current should be for i-v-t measurements.
        area in cm^2
        jmax in mA/cm^2
        imax in A (overrides jmax/area calc)
        returns value in A (defaults to 0.025A = 0.5cm^2 * 50 mA/cm^2)
        """
        ret_val = 0.5 * 0.05  # default guess is a 0.5 sqcm device operating at just above the SQ limit for Si
        if imax is not None:
            ret_val = imax
        elif (area is not None) and (jmax is not None):
            ret_val = jmax * area / 1000  # scale mA to A

        # enforce the global current limit
        if ret_val > self.current_limit:
            self.lg.warning("Overcurrent protection kicked in")
            ret_val = self.current_limit

        return ret_val

    def _connect_pcb(self, virtual=False, pcb_address=None):
        """Add control PCB attributes to class.

        PCB commands run in their own context manager so this isn't a real connect
        method. It just enables the PCB methods to function.

        Adds a class as an attribute rather than returning an object.

        Not necessary to append to list of connected instruments.

        Parameters
        ----------
        pcb_address : str
            Control PCB address string.
        """
        self.pcb_address = pcb_address
        if virtual == True:
            self.pcb = virt.pcb
        else:
            self.pcb = Pcb

    def _connect_motion(self, virtual=False, motion_address=None):
        """Add motion controller attributes to class.

        Motion commands run in their own context manager so this isn't a real connect
        method. It just enables the motion methods to function.

        Adds a class as an attribute rather than returning an object.

        Not necessary to append to list of connected instruments.

        Parameters
        ----------
        motion_address : str
            Control PCB address string.
        """
        self.motion_address = motion_address
        self.motion = motion
        if virtual == True:
            self.motion_pcb = virt.pcb
        else:
            self.motion_pcb = Pcb

    def connect_instruments(self, pcb_address=None, pcb_virt=False, motion_address=None, motion_virt=False):
        """Connect to instruments.

        If any instrument addresses are `None`, virtual (fake) instruments are
        "connected" instead.

        Parameters
        ----------
        smus : list of SMU config dicts
        pcb_address : str
            VISA resource name for the multiplexor and stage pcb. If `None` is
            given a virtual instrument is created.
        """

        if (pcb_address is not None) or (pcb_virt == True):
            self._connect_pcb(virtual=pcb_virt, pcb_address=pcb_address)

        if (motion_address is not None) or (motion_virt == True):
            self._connect_motion(virtual=motion_virt, motion_address=motion_address)

    def goto_pixel(self, pixel, mo):
        """Move to a pixel.

        Parameters
        ----------
        pixel : dict
            Pixel information dictionary.
        mo : class
            Motion object.

        Returns
        -------
        response : int
            Response code. 0 is good, everything else means fail.
        """
        if hasattr(self, "motion"):
            # ignore motion to position None and to places infinately far away
            # inf appears when the user wishes to disable motion for a specific pixel
            # in the layout configuration file
            there = pixel["pos"]
            if (there is not None) and (float("inf") not in there) and (float("-inf") not in there):
                mo.goto(there)
        return 0

    def select_pixel(self, mux_string=None, pcb=None):
        """manipulates the mux. returns nothing and throws a value error if there was a filaure"""
        if pcb is not None:
            if mux_string is None:
                mux_string = ["s"]  # empty call disconnects everything

            # ensure we have a list
            if isinstance(mux_string, str):
                selection = [mux_string]
            else:
                selection = mux_string

            pcb.set_mux(selection)
