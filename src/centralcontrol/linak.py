#!/usr/bin/env python3

import usb.core
import usb.util
import time
import sys


class Linak(object):
    # this is just a stripped down python port of Dawid Urbański's work here:
    # https://github.com/UrbanskiDawid/usb2lin06-HID-in-linux-for-LINAK-Desk-Control-Cable
    # Thanks Dawid!

    usb_idvendor = 0x12D3
    usb_idproduct = 0x0002

    HID_REPORT_SET = 0x09
    HID_REPORT_GET = 0x01

    RequestType_SetClassInterface = 0x21
    RequestType_GetClassInterface = 0xA1

    wValue_Init = 0x0303
    wValue_GetStatus = 0x0304
    wValue_Move = 0x0305
    wValue_GetExt = 0x0309

    StatusReport_ID = 0x4

    featureReportID_modeOfOperation = 3
    featureReportID_getLINdata = 4
    featureReportID_controlCBC = 5
    featureReportID_controlTD = 6
    featureReportID_controlCBD_TD = 8
    featureReportID_getLINdataExtended = 9

    featureReportID_modeOfOperation_default = 4

    empty_buf = None
    stage = None
    ready = False
    start_pos = None

    def __init__(self):
        # try to find the dongle
        stage = usb.core.find(idVendor=self.usb_idvendor, idProduct=self.usb_idproduct)

        if stage is None:
            raise ValueError("Can't find usb2lin06. Is it plugged in?")

        try:
            # detach kernel driver from interface 0
            stage.detach_kernel_driver(0)
        except Exception as e:
            pass  # already detached?

        # set the active configuration. With no arguments, the first
        # configuration will be the active one
        stage.set_configuration()

        # get an endpoint instance
        cfg = stage.get_active_configuration()
        intf = cfg[(0, 0)]

        out_filter = lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT

        out_ep = usb.util.find_descriptor(intf, custom_match=out_filter)

        if out_ep is None:
            raise ValueError("Output endpoint not found")

        self.empty_buf = bytearray(out_ep.wMaxPacketSize)
        self.stage = stage

        # init the controller
        self.start_pos = self.get_pos()
        self.setup()

        self.ready = True

    def get_pos(self):
        # form getStatus cmd
        msg = self.empty_buf
        pos = 0
        msg[pos : pos + 1] = [self.StatusReport_ID]

        status_msg = self.stage.ctrl_transfer(self.RequestType_GetClassInterface, self.HID_REPORT_GET, self.wValue_GetStatus, 0, msg)

        return status_msg[5] * 256 + status_msg[4]  # pick out position from status message

    def setup(self):
        msg = self.empty_buf

        pos = 0
        msg[pos : pos + 1] = [self.featureReportID_modeOfOperation]
        pos = 1
        msg[pos : pos + 1] = [self.featureReportID_modeOfOperation_default]
        pos = 2
        msg[pos : pos + 1] = [0x00]  # ?
        pos = 3
        msg[pos : pos + 1] = [0xFB]  # ?

        self.stage.ctrl_transfer(self.RequestType_SetClassInterface, self.HID_REPORT_SET, self.wValue_Init, 0, msg)
        time.sleep(0.2)

    def do_move(self, dest):
        if self.ready == False:
            raise (ValueError("Not ready to move :-("))

        msg = self.empty_buf
        msg[0:1] = [self.featureReportID_controlCBC]
        dest_uint16 = (dest).to_bytes(2, byteorder="little", signed=False)

        pos = 1
        msg[pos : pos + 2] = dest_uint16

        pos = 3
        msg[pos : pos + 2] = dest_uint16

        pos = 5
        msg[pos : pos + 2] = dest_uint16

        pos = 7
        msg[pos : pos + 2] = dest_uint16
        self.stage.ctrl_transfer(self.RequestType_SetClassInterface, self.HID_REPORT_SET, self.wValue_Move, 0, msg)
        time.sleep(0.2)

    def goto(self, step_goal):
        s.get_pos()
        s.do_move(step_goal)
        while True:
            here = s.get_pos()
            if here == step_goal:
                break
            s.do_move(step_goal)


if __name__ == "__main__":

    if len(sys.argv) != 2:
        # bad usage
        print(f"Usage: {sys.argv[0]} TARGET_STEP_VALUE")
        sys.exit(1)

    if sys.argv[1] == "home":
        goal = 2596
    else:
        goal = int(sys.argv[1])

    s = Linak()

    print(f"Trying to get to {goal} from {s.start_pos}...")
    s.do_move(goal)
    while True:
        here = s.get_pos()
        if here == goal:
            break
        s.do_move(goal)
    print(f"We have arrived at {here}.")

    # if here != s.start_pos:
    #  print(f'Now returning to {s.start_pos}...')
    #  s.goto(s.start_pos)
