"""The simulated WiFi gateway: serial #mLive lines in, MQTT messages out (the job of the ESP32 side)."""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from discovery import discovery_messages
from mapping import NoCells, to_mqtt
from mpm import MpmFilter
from schema import Info, Meas
from thingset import decode_live

log = logging.getLogger("relay")
SCHEMA_VERSION = 1

# The stream does not carry the limits; these are the LFP defaults (`?Conf` on the real device).
ASSUMED_LIMITS = {
    "cell_overvoltage_v": 3.8, "cell_undervoltage_v": 2.5, "chg_max_temp_c": 45.0,
    "dis_max_temp_c": 45.0, "temp_hysteresis_c": 5.0, "cell_failure_diff_v": 0.4,
}


class Relay:
    def __init__(self, lines: Iterator[str], host: str, port: int, device_id: str,
                 min_interval_s: float = 5.0, capacity_ah: float = 50.0,
                 clock: Callable[[], float] = time.monotonic, mpm: MpmFilter | None = None):
        self.lines, self.host, self.port = lines, host, port
        self.device_id, self.base = device_id, f"bms/{device_id}"
        self.min_interval_s, self.capacity_ah, self.clock = min_interval_s, capacity_ah, clock
        self.mpm = mpm or MpmFilter()      # which samples are worth telling MPM
        self.stats = {"lines": 0, "bad_crc": 0, "no_cells": 0, "invalid": 0, "published": 0, "mpm_published": 0}
        self.last_error = ""
        self.wifi_up = True            # the simulated radio; see set_wifi()
        self.connected = False
        self.cell_count = 0
        self._last_pub = -1e9
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="relay", daemon=True)
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"wifi-{device_id}")
        self.client.will_set(f"{self.base}/status", "offline", qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._publish_offline()
        self.client.loop_stop()
        self.client.disconnect()

    def _publish_offline(self) -> None:
        if self.connected:
            self.client.publish(f"{self.base}/status", "offline", qos=1, retain=True).wait_for_publish(2)

    def set_wifi(self, up: bool) -> None:
        """Simulate the radio dropping out: say goodbye and disconnect, or reconnect."""
        if up == self.wifi_up:
            return
        self.wifi_up = up
        if up:
            # after a deliberate disconnect paho's network thread has ended, so start it again
            self.client.connect_async(self.host, self.port, keepalive=30)
            self.client.loop_start()
        else:
            self._publish_offline()
            self.client.disconnect()
            self.client.loop_stop()

    # ------------------------------------------------------------- callbacks
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.error("broker refused the connection: %s", reason_code)
            return
        self.connected = True
        client.publish(f"{self.base}/status", "online", qos=1, retain=True)
        if self.cell_count:
            self._announce()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False

    def _announce(self) -> None:
        info = Info(schema=SCHEMA_VERSION, device_id=self.device_id, manufacturer="Libre Solar",
                    model="BMS C1 (via simulated WiFi gateway)", firmware="libre-solar-bms",
                    chemistry="LFP", cell_count=self.cell_count, capacity_ah=self.capacity_ah,
                    limits=ASSUMED_LIMITS)
        self.client.publish(f"{self.base}/info", info.model_dump_json(by_alias=True), qos=1, retain=True)
        for topic, payload in discovery_messages(self.device_id, self.base, self.cell_count):
            self.client.publish(topic, json.dumps(payload), qos=1, retain=True)

    # ------------------------------------------------------------- processing
    def process_line(self, line: str) -> dict | None:
        """One serial line -> a validated MQTT message dict, or None (and a counter) if unusable."""
        line = line.strip()
        if not line.startswith("#mLive"):
            return None                      # shell echo, log lines, other statements
        self.stats["lines"] += 1
        try:
            msg = to_mqtt(decode_live(line))
        except NoCells as e:
            self.stats["no_cells"] += 1
            self.last_error = str(e)
            return None
        except ValueError as e:
            self.stats["bad_crc"] += 1
            self.last_error = str(e)
            return None
        full = {"schema": SCHEMA_VERSION, "device_id": self.device_id,
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **msg}
        try:
            Meas.model_validate(full)
        except ValueError as e:
            self.stats["invalid"] += 1
            self.last_error = str(e)
            return None
        return full

    def handle(self, line: str) -> bool:
        """Process a line and publish it if due. Returns True if published."""
        full = self.process_line(line)
        if full is None:
            return False
        if not self.cell_count:
            self.cell_count = len(full["cell_voltages_v"])
            if self.connected:
                self._announce()
        now = self.clock()
        if not (self.connected and self.wifi_up):
            return False
        # MPM: judged on EVERY valid line (the 1 Hz stream), published only when it is worth a decision.
        event = self.mpm.check(full, now)
        if event:
            self.client.publish(f"{self.base}/mpm", json.dumps(event), qos=1)
            self.stats["mpm_published"] += 1
        # Home Assistant: the full measurement, rate limited by min_interval_s.
        if now - self._last_pub < self.min_interval_s:
            return False
        self._last_pub = now
        self.client.publish(f"{self.base}/meas", json.dumps(full), qos=0)
        self.stats["published"] += 1
        return True

    def _loop(self) -> None:
        for line in self.lines:
            if self._stop.is_set():
                return
            self.handle(line)
