import redis
import redis.asyncio as aredis
import asyncio

# import redis_annex
import json
from typing import Any, Generator
from queue import SimpleQueue as Queue
import logging
import centralcontrol.enums as en


class DBLink(object):
    """class to manage the link to our in-memory database"""

    db: redis.Redis
    inq: Queue  # input message queue
    lg: logging.Logger
    dat_seq: Generator[int, int | None, None]
    listen_streams: list[str] = []
    xread_task: asyncio.Task | None = None

    def __init__(self, db: redis.Redis, inq: None | Queue = None, lg: None | logging.Logger = None):
        self.db = db
        if inq:
            self.inq = inq
        if lg:
            self.lg = lg
        self.dat_seq = DBLink.counter_sequence()
        self.listen_streams.append("runs")

    def __enter__(self):
        """context manager for handling the inq relay setup/teardown"""
        assert self.inq, "The in-memory db relay input queue has not been defined"
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        """exit contect with listner cleanup"""
        self.stop_listening()
        return False

    def stop_listening(self):
        """cleanup listener and inq thread"""
        try:
            if self.xread_task:
                self.xread_task.cancel()
        except Exception as e:
            if self.lg:
                self.lg.debug(f"memdb unsubscribe fail: {repr(e)}")

    async def inq_handler(self):
        """shuttles incomming messages to the inq"""
        assert self.inq, "The in-memory db relay input queue has not been defined"
        listen_stream_ids = {s: "$" for s in self.listen_streams}
        ar = aredis.Redis(**self.db.get_connection_kwargs())
        keep_going = True
        while keep_going:
            self.xread_task = asyncio.create_task(ar.xread(streams=listen_stream_ids, block=0))
            try:
                streams = await self.xread_task
            except asyncio.CancelledError:
                keep_going = False
                streams = []

            for stream in streams:
                stream_name = stream[0]
                stream_items = stream[1]
                for stream_item in stream_items:
                    id = stream_item[0]
                    value = stream_item[1]
                    self.inq.put((stream_name, id, value))
                    listen_stream_ids[stream_name] = id  # prepare for the next message

        await ar.close()

        if self.lg:
            self.lg.debug("memdb inq relay finished")

    def multiput(self, table: str, data: list[tuple], col_names: list[str]) -> list[str]:
        dicts = [dict(zip(col_names, datum)) for datum in data]
        return [self.db.xadd(table, fields={"json": json.dumps(adict)}, maxlen=10000, approximate=True).decode() for adict in dicts]

    def registerer(self, device_dicts: list[dict], suid: int, smus: list, layouts: list | None = None) -> dict:
        """register substrates, devices, layouts, layout devices and setup slots with
        the db to get the ids for these to put into a lookup construct"""

        lu = {}  # a lookup construct to make looking things up later easier
        lu["setup_id"] = suid
        lu["slots"] = []
        lu["slot_ids"] = []
        lu["pads"] = []
        lu["slot_pad"] = []
        lu["user_labels"] = []
        lu["substrate_ids"] = []
        lu["layouts"] = []
        lu["layout_ids"] = []
        lu["layout_pad_ids"] = []
        lu["device_ids"] = []
        lu["smuis"] = []

        # register substrates, devices, layouts, layout devices, smus, setup slots, run-devices
        for device_dict in device_dicts:
            lu["slots"].append(device_dict["slot"])
            lu["pads"].append(device_dict["pad"])
            lu["slot_pad"].append((device_dict["slot"], device_dict["pad"]))
            lu["smuis"].append(device_dict["smui"])

            # register smu
            tool = {}
            tool["setup_id"] = suid
            tool["address"] = smus[device_dict["smui"]].address
            tool["idn"] = smus[device_dict["smui"]].idn
            smu_id = self.db.xadd("tbl_tools", fields={"json": json.dumps(tool)}, maxlen=100, approximate=True).decode()
            smus[device_dict["smui"]].id = smu_id

            # register setup slot
            slot = {}
            slot["name"] = device_dict["slot"]
            slot["setup_id"] = suid
            slot_id = self.db.xadd("tbl_setup_slots", fields={"json": json.dumps(slot)}, maxlen=10000, approximate=True).decode()
            device_dict["slid"] = slot_id
            lu["slot_ids"].append(slot_id)

            if "user_label" in device_dict:
                lu["user_labels"].append(device_dict["user_label"])

            if "layout" in device_dict:
                lu["layouts"].append(device_dict["layout"])
                layout_name = device_dict["layout"]
                if layouts:
                    # get layout version
                    layout_version = None
                    for layout in layouts:
                        if layout["name"] == layout_name:
                            layout_version = layout["version"]

                    # register layout
                    layout = {}
                    layout["name"] = layout_name
                    layout["version"] = layout_version
                    layout_id = self.db.xadd("tbl_layouts", fields={"json": json.dumps(layout)}, maxlen=10000, approximate=True).decode()
                    device_dict["loid"] = layout_id
                    lu["layout_ids"].append(layout_id)

                    # register layout-device
                    layout_device = {}
                    layout_device["layout_id"] = device_dict["loid"]
                    layout_device["pad_no"] = device_dict["pad"]
                    layout_device_id = self.db.xadd("tbl_layout_devices", fields={"json": json.dumps(layout_device)}, maxlen=10000, approximate=True).decode()
                    device_dict["ldid"] = layout_device_id
                    lu["layout_pad_ids"].append(layout_device_id)

                    if "user_label" in device_dict:
                        if device_dict["user_label"]:
                            lbl = device_dict["user_label"]
                        else:
                            lbl = None

                        # register substrate
                        substrate = {}
                        substrate["name"] = lbl
                        substrate["layout_id"] = device_dict["loid"]
                        substrate_id = self.db.xadd("tbl_substrates", fields={"json": json.dumps(substrate)}, maxlen=10000, approximate=True).decode()
                        device_dict["sbid"] = substrate_id
                        lu["substrate_ids"].append(substrate_id)

                        # register device
                        device = {}
                        device["substrate_id"] = device_dict["sbid"]
                        device["layout_device_id"] = device_dict["ldid"]
                        device_id = self.db.xadd("tbl_devices", fields={"json": json.dumps(device)}, maxlen=10000, approximate=True).decode()
                        device_dict["did"] = device_id
                        lu["device_ids"].append(device_id)

        return lu

    def new_light_cal(
        self,
        timestamp: str,
        sid: int,
        rid: int | None = None,
        temps: tuple[float] | None = None,
        raw_int: float | None = None,
        raw_spec: list[tuple[float]] | None = None,
        raw_int_spec: float | None = None,
        setpoint: tuple[float] | None = None,
        recipe: str | None = None,
        idn: str | None = None,
    ) -> str:
        to_upsert = {}
        to_upsert["time"] = timestamp
        to_upsert["setup_id"] = sid
        if rid:
            to_upsert["run_id"] = rid
        if temps:
            to_upsert["temperature"] = list(temps)
        to_upsert["raw_intensity"] = raw_int
        if raw_spec:
            to_upsert["raw_spectrum"] = [list(x) for x in raw_spec]
        if raw_int_spec:
            to_upsert["raw_int_spec"] = raw_int_spec
        if setpoint:
            to_upsert["setpoint"] = list(setpoint)
        if recipe:
            to_upsert["recipe"] = recipe
        if idn:
            to_upsert["idn"] = idn
        return self.db.xadd("tbl_light_cal", fields={"json": json.dumps(to_upsert)}, maxlen=100, approximate=True).decode()

    def putsmdat(self, data: list[tuple[float, float, float, int]], eid: int, kind: en.Event, rid: int) -> list[str]:
        """insert data row into a raw data table"""
        tbl = f"tbl_raw:{kind.value}:{rid}:{eid}"
        col_names = ["v", "i", "t", "s"]
        return self.multiput(tbl, data, col_names)

    @staticmethod
    def counter_sequence(start: int = 0) -> Generator[int, int | None, None]:
        """infinite upwards integer sequence generator"""
        c = start
        while True:
            yield c
            c += 1
