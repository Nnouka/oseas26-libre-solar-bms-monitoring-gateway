"""MQTT publisher: connects, announces the device, then streams measurements."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from battery import LIMITS, BatteryModel
from discovery import discovery_messages

log = logging.getLogger("publisher")
SCHEMA_VERSION = 1


class Publisher:
    def __init__(self, model: BatteryModel, host: str, port: int, device_id: str,
                 interval_s: float, speed: float):
        self.model, self.device_id = model, device_id
        self.interval_s, self.speed = interval_s, speed
        self.base = f"bms/{device_id}"
        self.connected = False
        self.published = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="bms-publisher")

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{device_id}")
        # Last Will: broker publishes this if we vanish without a clean disconnect
        self.client.will_set(f"{self.base}/status", "offline", qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.host, self.port = host, port

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self.client.connect_async(self.host, self.port, keepalive=30)  # retries forever if broker is down
        self.client.loop_start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.connected:
            self.client.publish(f"{self.base}/status", "offline", qos=1, retain=True).wait_for_publish(2)
        self.client.loop_stop()
        self.client.disconnect()

    # -- callbacks ---------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.error("MQTT connect refused: %s", reason_code)
            return
        self.connected = True
        log.info("connected to %s:%s", self.host, self.port)
        client.publish(f"{self.base}/status", "online", qos=1, retain=True)
        client.publish(f"{self.base}/info", json.dumps(self._info()), qos=1, retain=True)
        for topic, payload in discovery_messages(self.device_id, self.base, self.model.n):
            client.publish(topic, json.dumps(payload), qos=1, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        log.warning("disconnected from broker (%s); will retry", reason_code)

    # -- payloads ----------------------------------------------------------
    def _info(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "device_id": self.device_id,
            "manufacturer": "Libre Solar",
            "model": "BMS C1 (simulated)",
            "firmware": "simulator-0.1",
            "chemistry": "LFP",
            "cell_count": self.model.n,
            "capacity_ah": self.model.capacity_ah,
            "limits": LIMITS,
        }

    def _meas(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "device_id": self.device_id,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **self.model.snapshot(),
        }

    # -- main loop ---------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            self.model.step(self.interval_s * self.speed)   # model keeps running even if broker is down
            if self.connected:
                self.client.publish(f"{self.base}/meas", json.dumps(self._meas()), qos=0)
                self.published += 1
            time.sleep(self.interval_s)
