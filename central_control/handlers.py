"""Handlers for processing live data."""

import json

from mqtt_tools.queue_publisher import MQTTQueuePublisher


class DataHandler(MQTTQueuePublisher):
    """Publish list of data with MQTT client."""

    def __init__(self, idn=""):
        """Construct MQTT queue publisher.

        Parameters
        ----------
        idn : str
            Identity string to send with data.
        """
        super().__init__()
        self.idn = idn

    def handle_data(self, data):
        """Perform tasks with data.

        Parameters
        ----------
        data : list
            List of data.
        """
        payload = {
            "data": data,
            "clear": False,
            "id": self.idn,
        }
        # turn dict into string that mqtt can send
        payload = json.dumps(payload)
        self.append_payload(payload)

    def clear(self):
        """Clear plot."""
        payload = {"clear": True, "id": self.idn}
        payload = json.dumps(payload)
        self.append_payload(payload)


class SettingsHandler(MQTTQueuePublisher):
    """Publish settings with MQTT client."""

    def __init__(self):
        """Construct MQTT queue publisher."""
        super().__init__()

    def update_settings(self, folder, archive):
        """Update save settings.

        Parameters
        ----------
        folder : str
            Folder where data is saved.
        achive : str
            Network address where data can be backed up.
        """
        payload = {"folder": folder, "archive": archive}

        # turn dict into string that mqtt can send
        payload = json.dumps(payload)
        self.append_payload(payload)


class CacheHandler(MQTTQueuePublisher):
    """Publish cached files with MQTT client."""

    def __init__(self):
        """Construct MQTT queue publisher."""
        super().__init__()

    def save_cache(self, filename, contents):
        """Send data from cache.

        Parameters
        ----------
        finename : str
            Name of cached file.
        contents : str
            Contents of cached file.
        """
        payload = {"filename": filename, "contents": contents}

        # turn dict into string that mqtt can send
        payload = json.dumps(payload)
        self.append_payload(payload)


class StageHandler(MQTTQueuePublisher):
    """Publish list of data with MQTT client."""

    def __init__(self):
        """Construct MQTT queue publisher."""
        super().__init__()

    def handle_data(self, steps):
        """Perform tasks with data.

        Parameters
        ----------
        steps : list
            List of positions (steps) along each axis.
        """
        # turn dict into string that mqtt can send
        payload = json.dumps(steps)
        self.append_payload(payload)


class VoltageDataHandler(MQTTQueuePublisher):
    """Publish voltage vs. time data with MQTT client."""

    def __init__(self, idn=""):
        """Construct MQTT queue publisher.

        Parameters
        ----------
        idn : str
            Identity string to send with data.
        """
        super().__init__()
        self.idn = idn

    def handle_data(self, data):
        """Perform tasks with data.

        Parameters
        ----------
        data : list or str
            List of data from exp_1.
        """
        payload = {
            "x1": data[2],
            "y1": data[0],
            "clear": False,
            "id": self.idn,
        }
        # turn dict into string that mqtt can send
        payload = json.dumps(payload)
        self.append_payload(payload)

    def clear(self):
        """Clear plot."""
        payload = {"clear": True, "id": self.idn}
        payload = json.dumps(payload)
        self.append_payload(payload)


class IVDataHandler(MQTTQueuePublisher):
    """Publish current vs. voltage data with MQTT client."""

    def __init__(self, idn=""):
        """Construct MQTT queue publisher.

        Parameters
        ----------
        idn : str
            Identity string to send with data.
        """
        super().__init__()
        self.idn = idn

    def handle_data(self, data):
        """Perform tasks with data.

        Parameters
        ----------
        data : array
            Array of data from exp_2.
        """
        payload = {
            "data": data[:, :2].tolist(),
            "clear": False,
            "id": self.idn,
        }
        # turn dict into string that mqtt can send
        payload = json.dumps(payload)
        self.append_payload(payload)

    def clear(self):
        """Clear plot."""
        payload = {"clear": True, "id": self.idn}
        payload = json.dumps(payload)
        self.append_payload(payload)


class MPPTDataHandler(MQTTQueuePublisher):
    """Publish max power point tracking data with MQTT client."""

    def __init__(self, idn=""):
        """Construct MQTT queue publisher.

        Parameters
        ----------
        idn : str
            Identity string to send with data.
        """
        super().__init__()
        self.idn = idn

    def handle_data(self, data):
        """Perform tasks with data.

        Parameters
        ----------
        data : list
            List of data from exp_4.
        """
        payload = {
            "x1": data[2],
            "y1": data[0],
            "y2": data[1],
            "y3": data[0] * data[1],
            "clear": False,
            "id": self.idn,
        }
        payload = json.dumps(payload)
        self.append_payload(payload)

    def clear(self):
        """Clear plot."""
        payload = {"clear": True, "id": self.idn}
        payload = json.dumps(payload)
        self.append_payload(payload)


class CurrentDataHandler(MQTTQueuePublisher):
    """Publish current vs. time data with MQTT client."""

    def __init__(self, idn=""):
        """Construct MQTT queue publisher.

        Parameters
        ----------
        idn : str
            Identity string to send with data.
        """
        super().__init__()
        self.idn = idn

    def handle_data(self, data):
        """Perform tasks with data.

        Parameters
        ----------
        data : list
            List of data from exp_4.
        """
        payload = {
            "x1": data[2],
            "y1": data[1],
            "clear": False,
            "id": self.idn,
        }
        # turn dict into string that mqtt can send
        payload = json.dumps(payload)
        self.append_payload(payload)

    def clear(self):
        """Clear plot."""
        payload = {"clear": True, "id": self.idn}
        payload = json.dumps(payload)
        self.append_payload(payload)


class EQEDataHandler(MQTTQueuePublisher):
    """Publish EQE data with MQTT client."""

    def __init__(self, idn=""):
        """Construct MQTT queue publisher.

        Parameters
        ----------
        idn : str
            Identity string to send with data.
        """
        super().__init__()
        self.idn = idn

    def handle_data(self, data):
        """Perform tasks with data.

        Parameters
        ----------
        data : list
            List of data from exp_4.
        """
        # if calibration only return voltage measurement
        if len(data) == 13:
            payload = {
                "x1": data[1],
                "y1": data[-1],
                "y2": 0,
                "clear": False,
                "id": self.idn,
            }
        else:
            payload = {
                "x1": data[1],
                "y1": data[-2],
                "y2": data[-1],
                "clear": False,
                "id": self.idn,
            }
        payload = json.dumps(payload)
        self.append_payload(payload)

    def clear(self):
        """Clear plot."""
        payload = {"clear": True, "id": self.idn}
        payload = json.dumps(payload)
        self.append_payload(payload)
