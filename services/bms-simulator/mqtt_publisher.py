"""MQTT publisher: connects, announces the device, then streams measurements."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from ha_discovery import discovery_messages          # added in Part 8
from mpm import MpmFilter                         # added in 7.6
from schema import Info, Meas
from sim import Simulator

log = logging.getLogger("publisher")
SCHEMA_VERSION = 1


class Publisher:
    def __init__(self, model: Simulator, host: str, port: int, device_id: str,
                 interval_s: float, speed: float, mpm: MpmFilter | None = None):
        self.model = model
        self.host, self.port = host, port
        self.device_id = device_id
        self.interval_s, self.speed = interval_s, speed
        self.base = f"bms/{device_id}"       # every topic of this device starts with this
        self.mpm = mpm or MpmFilter()        # added in 7.6: decides what is worth telling MPM

        self.connected = False
        self.published = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="bms-publisher", daemon=True)

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{device_id}")
        # Last Will: if we vanish without a clean disconnect, the BROKER publishes this for us.
        self.client.will_set(f"{self.base}/status", "offline", qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        # connect_async + loop_start: paho retries in the background, even if the broker is down now.
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.connected:
            info = self.client.publish(f"{self.base}/status", "offline", qos=1, retain=True)
            info.wait_for_publish(timeout=2)
        self.client.loop_stop()
        self.client.disconnect()

    # ------------------------------------------------------------- callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.error("broker refused the connection: %s", reason_code)
            return
        self.connected = True
        log.info("connected to %s:%s", self.host, self.port)
        # Runs on EVERY (re)connect, so retained topics are always restored after a broker restart.
        client.publish(f"{self.base}/status", "online", qos=1, retain=True)
        client.publish(f"{self.base}/info", self._info_json(), qos=1, retain=True)
        # Added in Part 8: announce the Home Assistant entities (retained, so HA finds them after a restart)
        for topic, payload in discovery_messages(self.device_id, self.base, self.model.n):
            client.publish(topic, json.dumps(payload), qos=1, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        log.warning("disconnected from the broker (%s); paho will retry", reason_code)

    # ------------------------------------------------------------- payloads
    def _info_json(self) -> str:
        info = Info(
            schema=SCHEMA_VERSION,
            device_id=self.device_id,
            manufacturer="Libre Solar",
            model="BMS C1 (simulated)",
            firmware="simulator-0.1",
            chemistry="LFP",
            cell_count=self.model.n,
            capacity_ah=self.model.capacity_ah,
            limits=self.model.limits(),
        )
        return info.model_dump_json(by_alias=True)

    def meas_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "device_id": self.device_id,
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **self.model.snapshot(),
        }

    def _meas_json(self) -> str:
        # Validate before publishing: a bug in the model must never reach the broker.
        return Meas.model_validate(self.meas_dict()).model_dump_json(by_alias=True)

    def _publish_mpm(self) -> None:
        # Added in 7.6. Looks at every sample, publishes only the decision-worthy ones.
        event = self.mpm.check(self.meas_dict(), time.monotonic())
        if event:
            self.client.publish(f"{self.base}/mpm", json.dumps(event), qos=1)

    # ------------------------------------------------------------- main loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            # The battery lives on even while the broker is unreachable.
            self.model.step(self.interval_s * self.speed)
            if self.connected:
                self.client.publish(f"{self.base}/meas", self._meas_json(), qos=0)
                self.published += 1
                self._publish_mpm()                # added in 7.6
            self._stop.wait(self.interval_s)   # like sleep(), but wakes up at once on stop()
