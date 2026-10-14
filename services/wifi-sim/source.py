"""Where the serial lines come from.  Each source yields raw text lines, forever."""
from __future__ import annotations

import socket
import time
import urllib.request
from collections.abc import Iterator


def tcp_lines(host: str, port: int, retry_s: float = 2.0) -> Iterator[str]:
    """Lines from a TCP server, e.g. `socat` bridging the native_sim pseudo-terminal.  Reconnects."""
    while True:
        try:
            with socket.create_connection((host, port), timeout=10) as sock:
                sock.settimeout(None)
                with sock.makefile("r", encoding="ascii", errors="replace", newline="\n") as f:
                    for line in f:
                        yield line
        except OSError:
            pass
        time.sleep(retry_s)


def file_lines(path: str) -> Iterator[str]:
    """Lines from a file or pseudo-terminal path (Linux/WSL only)."""
    with open(path, "r", encoding="ascii", errors="replace") as f:
        for line in f:
            yield line


def serial_lines(port: str, baud: int = 115200) -> Iterator[str]:
    """Lines from a real serial port: the BMS C1's UART through a USB-serial adapter (COM5, /dev/ttyUSB0).

    Needs pyserial.  Reopens the port if the adapter is unplugged and plugged in again.
    """
    import serial          # imported here so the other sources work without pyserial

    while True:
        try:
            with serial.Serial(port, baud, timeout=2) as ser:
                while True:
                    raw = ser.readline()          # b"" after a 2 s timeout: just try again
                    if raw:
                        yield raw.decode("ascii", errors="replace")
        except (OSError, serial.SerialException):
            time.sleep(2.0)


def http_lines(url: str, every_s: float = 1.0) -> Iterator[str]:
    """Poll the `/live` endpoint of our own simulator, as if it were a serial port."""
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                yield resp.read().decode("ascii", errors="replace")
        except OSError:
            pass
        time.sleep(every_s)


def open_source(spec: str) -> Iterator[str]:
    """spec: serial:PORT[@BAUD] | tcp://host:port | file:/path | http://host:port/live"""
    if spec.startswith("serial:"):
        port, _, baud = spec[7:].partition("@")
        return serial_lines(port, int(baud or 115200))
    if spec.startswith("tcp://"):
        host, _, port = spec[6:].rpartition(":")
        return tcp_lines(host, int(port))
    if spec.startswith("file:"):
        return file_lines(spec[5:])
    if spec.startswith("http://") or spec.startswith("https://"):
        return http_lines(spec)
    raise ValueError(f"unknown source {spec!r}; use serial:PORT, tcp://host:port, file:/path or http://host:port/live")
