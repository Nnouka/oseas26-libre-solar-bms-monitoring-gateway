"""Steps the simulator on a timer when it does NOT publish MQTT itself (MQTT_ENABLED=false)."""
from __future__ import annotations

import threading

from sim import Simulator


class SimRunner:
    def __init__(self, model: Simulator, interval_s: float, speed: float):
        self.model, self.interval_s, self.speed = model, interval_s, speed
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="sim-runner", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.model.step(self.interval_s * self.speed)
            self._stop.wait(self.interval_s)
