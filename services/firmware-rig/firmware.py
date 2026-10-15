"""Runs the native_sim firmware and talks to it like a person at its shell would.

uart_0 (stdin/stdout) is the Zephyr shell: `i2c write/read` reach the emulated BQ76952.
uart_1 is a pseudo-terminal that carries the `#mLive` stream; its path is printed in the boot log.
"""
from __future__ import annotations

import logging
import queue
import re
import subprocess
import threading
import time

from encoder import CHIP_ADDR, BUS, i2c_write_cmds

log = logging.getLogger("firmware")

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
UART1 = re.compile(r"uart_1.*?pseudotty:\s*(\S+)")
HEXDUMP = re.compile(r"[0-9a-f]{8}:((?: {1,2}[0-9a-f]{2})+)")   # a second space after 8 bytes
# The shell's RX ring buffer holds 64 bytes (CONFIG_SHELL_BACKEND_SERIAL_RX_RING_BUFFER_SIZE) and is
# drained every 10 ms, so a long command is fed in small pieces.
CHUNK = 32
CHUNK_DELAY_S = 0.02


class FirmwareDead(RuntimeError):
    """The firmware process is not running (it exited, or was never started)."""


class FirmwareProcess:
    def __init__(self, command: list[str]):
        self.command = command
        self.uart1_path: str | None = None
        self._proc: subprocess.Popen | None = None
        self._lines: list[str] = []
        self._cond = threading.Condition()
        self._live: queue.Queue[str] = queue.Queue(maxsize=1000)
        self._stop = threading.Event()
        self._io = threading.Lock()                  # one shell conversation at a time
        self.settle_s = 0.05                         # pause after a batch so the shell has applied the last write

    @classmethod
    def native_sim(cls, binary: str, rt_ratio: float = 1.0) -> "FirmwareProcess":
        return cls([binary, f"-rt-ratio={rt_ratio}"])

    # ------------------------------------------------------------- lifecycle
    def start(self, boot_timeout: float = 15.0) -> None:
        self._proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, bufsize=0)
        threading.Thread(target=self._read_shell, name="fw-shell", daemon=True).start()
        deadline = time.monotonic() + boot_timeout
        while self.uart1_path is None:
            if not self.alive or time.monotonic() > deadline:
                raise FirmwareDead("firmware did not print its uart_1 path")
            time.sleep(0.05)
        threading.Thread(target=self._read_uart1, name="fw-uart1", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait(timeout=5)

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------- readers
    def _read_shell(self) -> None:
        for raw in iter(self._proc.stdout.readline, b""):
            line = ANSI.sub("", raw.decode(errors="replace")).strip("\r\n ")
            match = UART1.search(line)
            if match and self.uart1_path is None:
                self.uart1_path = match.group(1)
            with self._cond:
                self._lines.append(line)
                self._cond.notify_all()
        with self._cond:
            self._cond.notify_all()                  # EOF: wake waiters so they notice the process is gone

    def _read_uart1(self) -> None:
        buf = b""
        with open(self.uart1_path, "rb", buffering=0) as f:
            while not self._stop.is_set():
                try:
                    chunk = f.read(512)
                except OSError:                       # a pty reports EIO when the other side is gone
                    chunk = b""
                if not chunk:
                    time.sleep(0.05)
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode(errors="replace").strip()
                    if text.startswith("#mLive"):
                        try:
                            self._live.put_nowait(text)
                        except queue.Full:
                            self._live.get_nowait()   # drop the oldest: a slow consumer must not stall the firmware
                            self._live.put_nowait(text)

    def next_live_line(self, timeout: float = 5.0) -> str | None:
        try:
            return self._live.get(timeout=timeout)
        except queue.Empty:
            return None

    # ------------------------------------------------------------- shell
    def shell(self, command: str, timeout: float = 2.0, until=None) -> list[str]:
        """Send one command. Returns the output lines after it, once `until(lines)` is true or the shell is quiet."""
        if not self.alive:
            raise FirmwareDead("firmware is not running")
        with self._io:
            with self._cond:
                start = len(self._lines)
            data = (command + "\n").encode()
            for i in range(0, len(data), CHUNK):
                self._proc.stdin.write(data[i:i + CHUNK])
                self._proc.stdin.flush()
                time.sleep(CHUNK_DELAY_S)
            deadline = time.monotonic() + timeout
            quiet_since = time.monotonic()
            seen = start
            with self._cond:
                while True:
                    if len(self._lines) != seen:
                        seen, quiet_since = len(self._lines), time.monotonic()
                    out = self._lines[start:]
                    if until is not None and until(out):
                        return out
                    if until is None and time.monotonic() - quiet_since > 0.1 and out:
                        return out
                    if not self.alive:
                        raise FirmwareDead("firmware exited")
                    if time.monotonic() > deadline:
                        if until is not None:
                            raise TimeoutError(f"no answer to {command!r}")
                        return out
                    self._cond.wait(0.05)

    # ------------------------------------------------------------- chip access
    def write(self, reg: int, payload: bytes) -> None:
        for cmd in i2c_write_cmds(reg, payload):
            self.shell(cmd, timeout=0.3)

    def write_all(self, writes: list[tuple[int, bytes]]) -> None:
        """Send a whole register image back to back, then wait once for the shell to settle.

        Faster than `write` per register: used for the first image, which has to land before the
        firmware reads the cells in main() (about 0.5 s of firmware time after boot).
        """
        if not self.alive:
            raise FirmwareDead("firmware is not running")
        with self._io:
            for reg, payload in writes:
                for cmd in i2c_write_cmds(reg, payload):
                    data = (cmd + "\n").encode()
                    for i in range(0, len(data), CHUNK):
                        self._proc.stdin.write(data[i:i + CHUNK])
                        self._proc.stdin.flush()
                        time.sleep(CHUNK_DELAY_S)
        time.sleep(self.settle_s)

    def read_direct(self, reg: int, n: int) -> bytes:
        def complete(lines: list[str]) -> bool:
            return len(self._parse_hexdump(lines)) >= n

        lines = self.shell(f"i2c read {BUS} {CHIP_ADDR:#x} {reg:#04x} {n}", until=complete)
        return self._parse_hexdump(lines)[:n]

    @staticmethod
    def _parse_hexdump(lines: list[str]) -> bytes:
        data = b""
        for line in lines:
            m = HEXDUMP.search(line)
            if m:
                data += bytes(int(b, 16) for b in m.group(1).split())
        return data
