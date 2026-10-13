"""MQTT side: feeds bms/<id>/summary and bms/<id>/event into the outbox."""
from __future__ import annotations

import json
import logging

import paho.mqtt.client as mqtt

from outbox import Outbox

log = logging.getLogger("ingestor")


class Ingestor:
    def __init__(self, host: str, port: int, outbox: Outbox):
        self.host, self.port, self.outbox = host, port, outbox
        self.connected = False
        # Persistent session + QoS 1: the broker also queues messages while we are down.
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mpm-ingestor",
                                  protocol=mqtt.MQTTv311, clean_session=False)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self) -> None:
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.error("MQTT connect refused: %s", reason_code)
            return
        self.connected = True
        log.info("connected to %s:%s", self.host, self.port)
        client.subscribe([("bms/+/summary", 1), ("bms/+/event", 1)])

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self.connected = False
        log.warning("disconnected (%s); will retry", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            _, device_id, kind = msg.topic.split("/", 2)
            payload = json.loads(msg.payload.decode())
            if kind == "summary":
                self.outbox.add_summary(device_id, payload)
            elif kind == "event":
                self.outbox.add_event(device_id, payload)
        except Exception:
            log.exception("could not queue %s", msg.topic)
