"""MQTT Discovery and control bridge for the X5 publisher."""

import json
import logging
import os
import threading
from typing import Callable, Optional


log = logging.getLogger("x5.mqtt")

BROKER = os.getenv("X5_MQTT_BROKER", "iotlocal.lan")
PORT = int(os.getenv("X5_MQTT_PORT", "1883"))
USERNAME = os.getenv("X5_MQTT_USERNAME", "")
PASSWORD = os.getenv("X5_MQTT_PASSWORD", "")
PREFIX = os.getenv("X5_MQTT_PREFIX", "wificam/x5")
DISCOVERY_PREFIX = os.getenv("X5_MQTT_DISCOVERY_PREFIX", "homeassistant")
CLIENT_ID = os.getenv("X5_MQTT_CLIENT_ID", "wificam-x5-publisher")


class MqttBridge:
    def __init__(self, command: Callable[[str, int], bool]) -> None:
        self.command = command
        self.client = None
        self.states = {"led": None, "ir": None}
        self._lock = threading.Lock()

    @property
    def availability_topic(self) -> str:
        return PREFIX + "/availability"

    def _publish(self, topic: str, payload: str, retain: bool = True) -> None:
        if self.client is not None:
            self.client.publish(topic, payload, qos=1, retain=retain)

    def _publish_discovery(self) -> None:
        device = {
            "identifiers": ["wificam_x5"],
            "name": "WiFiCam X5",
            "manufacturer": "TXW817/X5",
            "model": "TXW817",
        }
        for name, label, command_id in (
            ("led", "LED", 3), ("ir", "IR", 7)
        ):
            config = {
                "name": "WiFiCam X5 " + label,
                "unique_id": "wificam_x5_" + name,
                "command_topic": PREFIX + "/" + name + "/set",
                "state_topic": PREFIX + "/" + name + "/state",
                "availability_topic": self.availability_topic,
                "payload_available": "online",
                "payload_not_available": "offline",
                "payload_on": "ON",
                "payload_off": "OFF",
                "optimistic": True,
                "qos": 1,
                "device": device,
                # Keep the mapping visible in the retained discovery document.
                "json_attributes_topic": PREFIX + "/attributes",
            }
            topic = "%s/switch/wificam_x5_%s/config" % (DISCOVERY_PREFIX, name)
            self._publish(topic, json.dumps(config, separators=(",", ":")))
            self.client.subscribe(config["command_topic"], qos=1)
            with self._lock:
                state = self.states[name]
            if state is not None:
                self._publish(PREFIX + "/" + name + "/state", state)
        self._publish(PREFIX + "/attributes", json.dumps({"led_command": 3, "ir_command": 7}))

    def _on_connect(self, client, _userdata, _flags, rc) -> None:
        if rc != 0:
            log.warning("MQTT connection failed rc=%s", rc)
            return
        log.info("MQTT connected broker=%s:%d", BROKER, PORT)
        self._publish_discovery()
        self._publish(self.availability_topic, "online")

    def _on_message(self, _client, _userdata, message) -> None:
        topic = message.topic
        for name in ("led", "ir"):
            if topic != PREFIX + "/" + name + "/set":
                continue
            value = message.payload.decode("utf-8", "replace").strip().upper()
            if value not in ("ON", "OFF"):
                log.warning("ignoring invalid MQTT %s command=%r", name, value)
                return
            enabled = 1 if value == "ON" else 0
            try:
                ok = self.command(name, enabled)
            except Exception:
                log.exception("MQTT %s command failed", name)
                ok = False
            if ok:
                with self._lock:
                    self.states[name] = value
                self._publish(PREFIX + "/" + name + "/state", value)
            else:
                log.warning("camera rejected MQTT %s command", name)
            return

    def run(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.warning("paho-mqtt is not installed; MQTT controls disabled")
            return
        self.client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
        if USERNAME:
            self.client.username_pw_set(USERNAME, PASSWORD)
        self.client.will_set(self.availability_topic, "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        try:
            self.client.connect(BROKER, PORT, keepalive=30)
            self.client.loop_forever()
        except Exception:
            log.exception("MQTT bridge stopped; RTSP publisher continues")

