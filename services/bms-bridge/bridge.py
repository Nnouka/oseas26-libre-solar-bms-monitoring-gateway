"""MQTT bridge: raw `meas` in -> `summary` (retained, low rate) and `event` out."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

import ha_discovery
from logic import DeviceTracker

log = logging.getLogger("bridge")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Bridge:
    def __init__(self, host: str, port: int, summary_interval_s: float, time_scale: float):
        self.host, self.port = host, port
        self.summary_interval_s, self.time_scale = summary_interval_s, time_scale
        self.trackers: dict[str, DeviceTracker] = {}
        self.connected = False
        self._lock = threading.Lock()
        self._stop = threading.Event()

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bms-bridge")
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()
        threading.Thread(target=self._summary_loop, daemon=True, name="summary-timer").start()

    def stop(self) -> None:
        self._stop.set()
        self.client.loop_stop()
        self.client.disconnect()

    # -- callbacks ---------------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.error("MQTT connect refused: %s", reason_code)
            return
        self.connected = True
        log.info("connected to %s:%s", self.host, self.port)
        client.subscribe([("bms/+/meas", 0), ("bms/+/info", 1), ("bms/+/status", 1)])

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        log.warning("disconnected (%s); will retry", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            _, device_id, kind = msg.topic.split("/", 2)
            raw = msg.payload.decode()
            with self._lock:
                tracker = self.trackers.get(device_id)
                if tracker is None:
                    tracker = self.trackers[device_id] = DeviceTracker(device_id, self.time_scale)
                    for topic, payload in ha_discovery.messages(device_id):
                        client.publish(topic, json.dumps(payload), qos=1, retain=True)
                    log.info("new device: %s", device_id)

                if kind == "meas":
                    events, summary_now = tracker.on_meas(json.loads(raw))
                    for ev in events:
                        self._publish_event(device_id, ev)
                    if summary_now:             # fault/health change: report immediately
                        self._publish_summary(tracker)
                elif kind == "info":
                    tracker.set_info(json.loads(raw))
                elif kind == "status":
                    ev = tracker.on_status(raw, now_iso())
                    if ev:
                        self._publish_event(device_id, ev)
        except Exception:  # never let one bad message kill the bridge
            log.exception("failed to handle %s", msg.topic)

    # -- publishing --------------------------------------------------------
    def _publish_event(self, device_id: str, ev: dict) -> None:
        self.client.publish(f"bms/{device_id}/event", json.dumps(ev), qos=1, retain=False)
        log.info("EVENT %s %s %s", device_id, ev["type"], ev.get("fault") or "")

    def _publish_summary(self, tracker: DeviceTracker) -> None:
        summary = tracker.summary()
        if summary is None:
            return
        self.client.publish(f"bms/{tracker.device_id}/summary", json.dumps(summary), qos=1, retain=True)
        tracker.last_summary_mono = time.monotonic()
        log.info("SUMMARY %s health=%s soc=%s", tracker.device_id, summary["health"], summary["soc_pct"])

    def _summary_loop(self) -> None:
        """Quiet when normal: one summary per interval; faults go out immediately (see _on_message)."""
        while not self._stop.wait(1.0):
            if not self.connected:
                continue
            with self._lock:
                for tracker in self.trackers.values():
                    if time.monotonic() - tracker.last_summary_mono >= self.summary_interval_s:
                        self._publish_summary(tracker)
