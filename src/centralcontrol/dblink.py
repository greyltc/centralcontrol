import redis
import redis_annex
import json
from typing import Any, Generator
from queue import SimpleQueue as Queue
import threading
import logging
import centralcontrol.enums as en

# class MemDBClient(object):
#     db: redis.Redis
#     def __init__(self, db: redis.Redis):
#         self.db = db

#     setup_listener_


class DBLink(object):
    """class to manage the link to our in-memory database"""

    db: redis.Redis
    inq: Queue  # input message queue
    p: redis.client.PubSub  # publish/subscribe object for
    inq_thread: threading.Thread
    lg: logging.Logger
    dat_seq: Generator[int, int | None, None]

    def __init__(self, db: redis.Redis, inq: None | Queue = None, lg: None | logging.Logger = None):
        self.db = db
        if inq:
            self.inq = inq
        if lg:
            self.lg = lg
        self.dat_seq = DBLink.counter_sequence()

    def __enter__(self):
        """context manager for handling the inq relay setup/teardown"""
        assert self.inq, "The in-memory db relay input queue has not been defined"
        self.p = self.db.pubsub()
        listen_channels = []
        listen_channels.append("runs:new")  # a new run has been initiated
        self.p.subscribe(*listen_channels)
        self.inq_thread = threading.Thread(target=self.inq_handler, daemon=False)
        self.inq_thread.start()

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        """exit contect with listner cleanup"""
        self.stop_listening()
        return False

    def stop_listening(self):
        """cleanup listener and inq thread"""
        try:
            self.p.unsubscribe()
        except Exception as e:
            if self.lg:
                self.lg.debug(f"memdb unsubscribe fail: {repr(e)}")
        try:
            self.inq_thread.join(1.0)
        except Exception as e:
            if self.lg:
                self.lg.debug(f"memdb inq thread join fail: {repr(e)}")

    def inq_handler(self):
        """shuttles incomming messages to the inq"""
        assert self.p, "The in-memory db listener has not been defined"
        assert self.inq, "The in-memory db relay input queue has not been defined"
        for message in self.p.listen():
            self.inq.put(message)
        if self.lg:
            self.lg.debug("memdb inq relay finished")

    def upsert(self, tbl: str, val: Any, id: int | None = None, expect_mod: bool | None = None) -> int | None:
        key = tbl.removeprefix("tbl_")
        if id is None:  # unique insert
            ret, mod = redis_annex.uadd(self.db, key, json.dumps(val))
        else:  # update
            mod = True
            assert isinstance(val, dict), "Upsert-update only works with dicts"
            # TODO: might consider using redis json extension for this...
            old_val_bin = self.db.zrange(key, id, id)[0]
            assert isinstance(old_val_bin, bytes), "Failed on update in upsert"
            contents = json.loads(old_val_bin)
            for ckey, new_val in val.items():
                contents[ckey] = new_val
            if isinstance(self.db.zadd(key, {json.dumps(contents): id}), int):
                ret = id
            else:  # zadd fail
                ret = None

        # check the expected mod state
        if expect_mod is not None:
            if mod != expect_mod:
                ret = None
        return ret

    def insert(self, tbl: str, val: Any, id: int | None = None) -> int | None:
        key = tbl.removeprefix("tbl_")
        if id is None:  # normal insert
            ret = redis_annex.add(self.db, key, json.dumps(val))
        else:  # update
            assert isinstance(val, dict), "Upsert-update only works with dicts"
            old_val_bin = self.db.lindex(key, id)
            assert isinstance(old_val_bin, bytes), "Failed on update in insert"
            contents = json.loads(old_val_bin)
            for ckey, new_val in val.items():
                contents[ckey] = new_val
            if isinstance(self.db.lset(key, id, json.dumps(contents)), int):
                ret = id
            else:  # zadd fail
                ret = None
        return ret

    def multiput(self, table: str, data: list[tuple], col_names: list[str], upsert: bool = True) -> list[int | None]:
        dicts = [dict(zip(col_names, datum)) for datum in data]
        if upsert:
            ret = [self.upsert(table, adict) for adict in dicts]
        else:  # insert
            ret = [self.insert(table, adict) for adict in dicts]
        return ret

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
            smu_id = self.upsert("tbl_tools", tool)
            assert isinstance(smu_id, int), "Registering smu failed"
            smus[device_dict["smui"]].id = smu_id

            # register setup slot
            slot = {}
            slot["name"] = device_dict["slot"]
            slot["setup_id"] = suid
            slot_id = self.upsert("tbl_setup_slots", slot)
            assert isinstance(slot_id, int), "Registering slot failed"
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
                    layout_id = self.upsert("tbl_layouts", layout)
                    assert isinstance(layout_id, int), "Registering layout failed"
                    device_dict["loid"] = layout_id
                    lu["layout_ids"].append(layout_id)

                    # register layout-device
                    layout_device = {}
                    layout_device["layout_id"] = device_dict["loid"]
                    layout_device["pad_no"] = device_dict["pad"]
                    layout_device_id = self.upsert("tbl_layout_devices", layout_device)
                    assert isinstance(layout_device_id, int), "Registering layout device failed"
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
                        substrate_id = self.upsert("tbl_substrates", substrate)
                        assert isinstance(substrate_id, int), "Registering substrate failed"
                        device_dict["sbid"] = substrate_id
                        lu["substrate_ids"].append(substrate_id)

                        # register device
                        device = {}
                        device["substrate_id"] = device_dict["sbid"]
                        device["layout_device_id"] = device_dict["ldid"]
                        device_id = self.upsert("tbl_devices", device)
                        assert isinstance(device_id, int), "Registering device failed"
                        device_dict["did"] = device_id
                        lu["device_ids"].append(device_id)

        return lu

    def new_light_cal(
        self,
        sid: int,
        rid: int | None = None,
        temps: tuple[float] | None = None,
        raw_int: float | None = None,
        raw_spec: list[tuple[float]] | None = None,
        raw_int_spec: float | None = None,
        setpoint: tuple[float] | None = None,
        recipe: str | None = None,
        idn: str | None = None,
    ) -> int | None:
        to_upsert = {}
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
        return self.upsert(f"tbl_light_cal", to_upsert)

    def putsmdat(self, data: list[tuple[float, float, float, int]], eid: int, kind: en.Event, table_counter: int = 1) -> list[int | None]:
        """insert data row into a raw data table"""
        to_put = []
        tbl = f"tbl_{kind.value}_raw_{table_counter}"
        col_names = ["eid", "dcs", "v", "i", "t", "s"]
        for row in data:
            to_put.append((eid, next(self.dat_seq)) + row)
        return self.multiput(tbl, to_put, col_names, upsert=False)

    @staticmethod
    def counter_sequence(start: int = 0) -> Generator[int, int | None, None]:
        """infinite upwards integer sequence generator"""
        c = start
        while True:
            yield c
            c += 1
