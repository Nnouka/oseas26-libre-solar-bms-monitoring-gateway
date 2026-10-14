"""The rig: our battery model plays the cells, the real firmware plays the BMS.

Each tick: step the model -> encode the reading -> write it into the firmware's emulated BQ76952. The
switch state the firmware answers with (from its #mLive stream) is fed into the next model step.
Chip auto-balancing is not emulated: the bleed mask stays 0.
The firmware's `#mLive` stream is re-served over TCP, exactly where a relay would read a real C1's UART.
"""
from __future__ import annotations

import json
import logging
import socket
import threading

from battery import Battery
from encoder import encode, encode_safety
from protections import ChipProtections

log = logging.getLogger("rig")

# rBmsState -> (charge FET on, discharge FET on). The firmware's own state machine decides this, and it
# reports it in every #mLive line, so the rig does not read the chip's subcommand memory (reading through
# the shared subcommand window would overwrite a write the firmware has in flight).
FET_STATES = {0: (False, False), 1: (True, False), 2: (False, True), 3: (True, True)}   # OFF CHG DIS NORMAL


def parse_live(line: str) -> dict | None:
    """The Meas object of a `#mLive {...} <crc>` line, or None if it is not a complete one."""
    if not line.startswith("#mLive "):
        return None
    try:
        return json.loads(line[len("#mLive "):].rsplit(" ", 1)[0])["Meas"]
    except (ValueError, KeyError, IndexError):
        return None


class Rig:
    def __init__(self, firmware, battery: Battery, interval_s: float = 1.0, speed: float = 1.0,
                 protections: ChipProtections | None = None):
        self.firmware, self.battery = firmware, battery
        self.interval_s, self.speed = interval_s, speed
        self.protections = protections or ChipProtections(capacity_ah=battery.nominal_ah)
        self.chg_on = self.dis_on = True
        self.bleed_mask = 0
        self.ticks = 0
        self.last_error = ""

    def _image(self, reading) -> list[tuple[int, bytes]]:
        return encode(reading) + encode_safety(self.protections.update(reading))

    def boot(self) -> None:
        """First register image. It must land before the firmware reads the cells in main()."""
        self.firmware.write_all(self._image(self.battery.read_chip()))

    def tick(self) -> None:
        if not self.firmware.alive:
            raise RuntimeError("firmware is not running")
        reading = self.battery.step(self.interval_s * self.speed, self.chg_on, self.dis_on, self.bleed_mask)
        self.firmware.write_all(self._image(reading))
        self.ticks += 1

    def observe(self, live_line: str) -> None:
        """Learn the firmware's switch state from its #mLive line (garbled lines are ignored)."""
        meas = parse_live(live_line)
        if meas is not None and meas.get("rBmsState") in FET_STATES:
            self.chg_on, self.dis_on = FET_STATES[meas["rBmsState"]]


    def run(self, stop: threading.Event) -> None:
        """Tick every `interval_s` of real time until stopped, or until the firmware is gone."""
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:                   # the firmware died or the shell broke: say why and stop
                self.last_error = str(e)
                log.error("rig stopped: %s", e)
                return
            stop.wait(self.interval_s)


    def pump(self, stop: threading.Event, server) -> None:
        """Move every #mLive line from the firmware to the rig (switch state) and to the TCP clients."""
        while not stop.is_set() and self.firmware.alive:
            line = self.firmware.next_live_line(timeout=0.2)
            if line:
                self.observe(line)
                server.broadcast(line)


class LiveServer:
    """Sends every `#mLive` line to all connected TCP clients (what `socat` does in the tutorial)."""

    def __init__(self, host: str = "0.0.0.0", port: int = 3333):
        self._sock = socket.socket()
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self.port = self._sock.getsockname()[1]
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self) -> None:
        self._sock.listen()
        threading.Thread(target=self._accept, name="live-accept", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for c in self._clients:
                c.close()
            self._clients.clear()
        self._sock.close()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._sock.accept()
            except OSError:
                return
            with self._lock:
                self._clients.append(client)

    def broadcast(self, line: str) -> None:
        data = (line.rstrip("\r\n") + "\n").encode()
        with self._lock:
            alive = []
            for c in self._clients:
                try:
                    c.sendall(data)
                    alive.append(c)
                except OSError:
                    c.close()                   # a vanished client must not stop the others
            self._clients[:] = alive
