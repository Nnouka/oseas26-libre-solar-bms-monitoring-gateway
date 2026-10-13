# Part 10: The WiFi gateway simulator

**Goal.** Build a second service, `services/wifi-sim/`: the **production gateway** between the BMS's serial `#mLive` lines and the network. It checks the CRC, converts the lines to the team's MQTT messages (including Home Assistant discovery) and sends them to the broker. It is the **only** component that publishes MQTT in the final stack.
It is called a simulator only because the radio is simulated on a laptop; the logic is what a real deployment needs, and swapping the source from the simulator to a real C1 is a one-line change.

**Time.** 2 to 3 hours.

## Ideas

**Why this module exists.** The real Libre Solar firmware has no WiFi and no MQTT (see firmware-findings.md). A real deployment needs a small gateway between the BMS's serial port and the network. Think of it as the ESP32's job, in a box.
Building it as its own service gives you a clean seam:

```text
 native_sim (real firmware)  --serial #mLive-->  \
                                                    wifi-sim  --MQTT over the network-->  broker -> Home Assistant, ...
 bms-simulator (Parts 1-9)   --/live (same line)-> /
```

Both sources produce the *same* line format, so the gateway does not know or care which one it reads. That is the point of Part 3.

**Four sources.** `SOURCE` is a URL-like string. This is the **only** thing that differs between the demo and production:

| `SOURCE` | Reads from | Use for |
| --- | --- | --- |
| `serial:COM5` or `serial:/dev/ttyUSB0@115200` | a real serial port: the C1's UART (the firmware's `uart0` runs at 115200) through a USB-serial adapter | **the real BMS** |
| `http://host:8000/live` | the simulator's `/live` endpoint, polled once a second | the laptop demo with no extra tools |
| `tcp://host:4001` | a TCP server that carries the serial lines | native_sim, via a bridge (below) |
| `file:/dev/pts/N` | a pseudo-terminal path | native_sim on Linux/WSL, when the gateway runs there too |

**What the gateway must do with a stream that is not clean.** A serial line can carry shell echo, log text, half lines, corrupted characters. The gateway therefore (1) ignores everything that is not a `#mLive` statement, (2) verifies the CRC and
**counts** the failures instead of crashing, (3) validates the result against the schema before it goes on the network, and (4) rate limits (the firmware sends 1 line per second, the cloud wants one every 5 s).

**The radio.** WiFi drops out. `POST /wifi/down` makes the gateway publish `offline` and disconnect; `POST /wifi/up` brings it back. Use it to see Home Assistant mark the device unavailable and recover. During an outage the real firmware keeps running and, in this simple
version, we just lose the readings. Buffering is a stretch goal.

**What native_sim can and cannot do here.** Run the gateway against the real `native_sim` and you will see 17 valid lines, every CRC correct, and *zero* published messages. The emulated chip has no cells, and the schema requires at least one cell voltage. That is the correct
result. The gateway reports it in `/stats` (`no_cells`) and does not invent data. To get realistic numbers from the *real firmware* you would need to feed the emulated chip's registers from the battery model, a C/Zephyr job (stretch goal in Part 12).

**Code reuse.** The service has its own copies of `thingset.py`, `schema.py`, `discovery.py` and a mapping. Copying is deliberate for now: each service builds and ships alone. A shared package is the later clean-up (Part 12).

## Steps

### 10.1 Project

```bash
mkdir services/wifi-sim
cd services/wifi-sim
```

Copy `pyproject.toml` from `services/bms-simulator`, change the name to `wifi-sim`, and add `"pyserial>=3.5"` to the dependencies (the library for real serial ports), then `uv sync` (this creates its own `uv.lock`). Copy these files unchanged from `services/bms-simulator`: `thingset.py`, `schema.py`, `discovery.py`, `mpm.py` (Part 7.6), `tests/conftest.py`, `tests/test_thingset.py`, `tests/test_discovery.py`, `tests/test_mpm.py`.
In `test_discovery.py` the test file does not need the simulator, so check there is no import of `sim` or `publisher`; run `uv run pytest -q`, which should give `12 passed`.

### 10.2 Tests first

`tests/test_relay.py`:

```python
import pytest
from fastapi.testclient import TestClient

from mapping import NoCells, to_mqtt
from relay import Relay
from schema import Meas
from test_thingset import NATIVE_SIM_LINE
from thingset import decode_live, encode_live

GOOD = {
    "rPackVoltage_V": 26.16, "rStackVoltage_V": 26.16, "rPackCurrent_A": 3.2,
    "rCellTemps_degC": [29.0, 28.4], "rICTemp_degC": 31.0, "rMOSFETTemp_degC": 29.0,
    "rSOC_pct": 57.7, "rErrorFlags": 1 << 9, "rBmsState": 3,
    "rCellVoltages_V": [3.269, 3.271, 3.27, 3.268, 3.268, 3.27, 3.271, 3.269] + [0.0] * 8,
    "rCellAvgVoltage_V": 3.27, "rCellMinVoltage_V": 3.268, "rCellMaxVoltage_V": 3.271,
    "rBalancingStatus": 0,
}


def relay(**kw) -> Relay:
    ticks = iter(range(0, 10_000, 5))                   # a fake clock that advances 5 s per call
    return Relay(iter(()), "localhost", 1883, "dev1", clock=lambda: float(next(ticks)), **kw)


def test_native_sim_empty_chip_is_recognised_not_published():
    r = relay()
    assert r.process_line(NATIVE_SIM_LINE) is None
    assert r.stats["no_cells"] == 1 and r.stats["bad_crc"] == 0
    with pytest.raises(NoCells):
        to_mqtt(decode_live(NATIVE_SIM_LINE))


def test_a_corrupted_line_is_counted_and_dropped():
    r = relay()
    assert r.process_line(encode_live(GOOD).replace("57.7", "57.8")) is None
    assert r.stats["bad_crc"] == 1


def test_shell_noise_is_ignored_silently():
    r = relay()
    assert r.process_line("uart:~$ ?Meas") is None
    assert r.stats["lines"] == 0


def test_a_good_line_becomes_a_valid_schema_message():
    msg = relay().process_line(encode_live(GOOD))
    assert msg is not None
    Meas.model_validate(msg)
    assert msg["errors"] == ["CHG_OVERTEMP"] and len(msg["cell_voltages_v"]) == 8


def test_nothing_is_published_while_not_connected_but_the_cell_count_is_learned():
    r = relay()
    assert r.handle(encode_live(GOOD)) is False
    assert r.cell_count == 8


def test_publishes_to_the_meas_topic_when_connected():
    r = relay(min_interval_s=5.0)
    r.connected = True
    sent = []
    r.client.publish = lambda topic, *a, **k: sent.append(topic)   # no network in a unit test
    r._announce = lambda: None
    assert r.handle(encode_live(GOOD)) is True
    assert sent == ["bms/dev1/mpm", "bms/dev1/meas"]       # the first sample also goes to MPM


def test_mpm_hears_about_changes_not_about_every_line():
    r = relay(min_interval_s=100.0)
    r.connected = True
    sent = []
    r.client.publish = lambda topic, *a, **k: sent.append(topic)
    r._announce = lambda: None
    for _ in range(3):
        r.handle(encode_live(GOOD))
    assert sent.count("bms/dev1/mpm") == 1                  # "first"; nothing changed after that
    r.handle(encode_live({**GOOD, "rErrorFlags": 0}))       # the fault clears
    assert sent.count("bms/dev1/mpm") == 2


def test_publishing_is_rate_limited():
    r = relay(min_interval_s=100.0)          # the fake clock moves 5 s per call
    r.connected = True
    r.client.publish = lambda *a, **k: None
    r._announce = lambda: None
    line = encode_live(GOOD)
    assert [r.handle(line) for _ in range(3)] == [True, False, False]


def test_wifi_down_stops_publishing():
    r = relay()
    r.connected = True
    r.wifi_up = False
    assert r.handle(encode_live(GOOD)) is False


def test_api_health_and_bad_wifi_state():
    import main
    c = TestClient(main.app)
    assert c.get("/health").json()["service"] == "wifi-sim"
    assert c.post("/wifi/sideways").status_code == 400
```

Look at how the tests avoid the network: `relay()` makes a `Relay` with an empty line source and a fake clock, and `r.client.publish` is replaced by a function that records the topic. You test the *logic* (counting, validating, rate limiting) without a broker.

### 10.3 `mapping.py` in the gateway

Same job as in Part 6, with one difference: the gateway has no firmware module to import, and must say clearly when a line is valid but has **no cells**. Write `to_mqtt` so that it raises a dedicated `NoCells` exception in that case.

<details>
<summary>Solution: mapping.py</summary>

```python
"""ThingSet `Meas` items -> the team's MQTT `meas` message.  Same rules as the simulator's mapping."""
from __future__ import annotations

NOT_CONNECTED_V = 0.5          # a cell channel below this voltage has no cell
STATE_NAMES = ["OFF", "CHG", "DIS", "NORMAL", "SHUTDOWN"]
ERROR_BITS = [
    "CELL_UNDERVOLTAGE", "CELL_OVERVOLTAGE", "SHORT_CIRCUIT", "DIS_OVERCURRENT",
    "CHG_OVERCURRENT", "OPEN_WIRE", "DIS_UNDERTEMP", "DIS_OVERTEMP", "CHG_UNDERTEMP",
    "CHG_OVERTEMP", "INT_OVERTEMP", "CELL_FAILURE", "DIS_OFF", "CHG_OFF", "FET_OVERTEMP",
]


class NoCells(ValueError):
    """The line is valid, but no battery cell is connected (what native_sim reports)."""


def error_names(flags: int) -> list[str]:
    return [name for bit, name in enumerate(ERROR_BITS) if flags >> bit & 1]


def to_mqtt(meas: dict) -> dict:
    cells = [round(v, 3) for v in meas["rCellVoltages_V"] if v > NOT_CONNECTED_V]
    if not cells:
        raise NoCells("no cell connected (empty chip)")
    return {
        "state": STATE_NAMES[meas["rBmsState"]],
        "pack_voltage_v": round(meas["rPackVoltage_V"], 2),
        "pack_current_a": round(meas["rPackCurrent_A"], 2),
        "soc_pct": round(meas["rSOC_pct"], 1),
        "cell_voltages_v": cells,
        "cell_temps_c": [round(t, 1) for t in meas["rCellTemps_degC"]],
        "mosfet_temp_c": round(meas["rMOSFETTemp_degC"], 1),
        "balancing_mask": meas["rBalancingStatus"],
        "errors": error_names(meas["rErrorFlags"]),
    }
```

</details>

### 10.4 `source.py`

Four generator functions and an `open_source(spec)` that picks one. A *generator* (`yield`) hands out one line at a time and remembers where it was. `tcp_lines` must **reconnect** if the connection drops. `http_lines` polls.

Hints: for the serial source `serial.Serial(port, baud, timeout=2)` and `ser.readline()` (it returns `b""` after a timeout, so just loop; import `serial` inside the function, so the other sources work without it). `socket.create_connection((host, port))` and `sock.makefile("r")` gives you a file object you can loop over line by line. `urllib.request.urlopen(url)` is enough for HTTP, so no extra dependency. Catch `OSError`, wait, and try again.

<details>
<summary>Solution: source.py</summary>

```python
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
    if spec.startswith("http://"):
        return http_lines(spec)
    raise ValueError(f"unknown source {spec!r}; use serial:PORT, tcp://host:port, file:/path or http://host:port/live")
```

</details>

### 10.5 `relay.py`

It resembles the Part 7 publisher, with the same lifecycle (`start`, `stop`), last will and retained status. The differences are the input and the additions below.

- `process_line(line)`: strip; ignore anything not starting with `#mLive`; count it; `decode_live` (a CRC failure raises `ValueError`, count `bad_crc`); `to_mqtt` (`NoCells`: count `no_cells`); add `schema`, `device_id`, `ts`; validate with `Meas` (count `invalid`). Return the dict or `None`.
  Note the `except` order: `NoCells` is a kind of `ValueError`, so it must be caught **first**.
- `handle(line)`: call `process_line`; learn `cell_count` from the first good line (the stream does not tell you; and announce `info` and discovery then); if connected and WiFi is up, hand the message to the MPM filter (**every** valid line, so a fault is never missed because of rate limiting) and publish it to `bms/<id>/mpm` if the filter says so, then publish `meas` for Home Assistant only if `min_interval_s` has passed since the last `meas`.
- `set_wifi(up)`: `up=False`: publish `offline`, `disconnect()`, `loop_stop()`. `up=True`: `connect_async()` and `loop_start()` again. (After a deliberate disconnect paho's thread has ended, and a plain `reconnect()` does nothing. We found that out by trying it.)
- `_loop`: `for line in self.lines: self.handle(line)`.
- Because the stream has no `info`, `ASSUMED_LIMITS` carries the LFP defaults. They are labelled as assumed in the code.

<details>
<summary>Solution: relay.py</summary>

```python
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
```

</details>

### 10.6 `main.py`

The same pattern as Part 9: read the environment, create the `Relay`, start/stop in `lifespan`. Endpoints: `GET /health` (`status`, `service: "wifi-sim"`, `mqtt_connected`, `wifi_up`, `source`), `GET /stats` (the counters, `last_error`, `cell_count`), `POST /wifi/{state}` (`up` or `down`, else HTTP 400).
Variables: `SOURCE` (default `http://localhost:8000/live`), `MQTT_HOST`, `MQTT_PORT`, `DEVICE_ID`, `PUBLISH_INTERVAL_S`, `CAPACITY_AH`.

<details>
<summary>Solution: main.py</summary>

```python
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from relay import Relay
from source import open_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

SOURCE = os.getenv("SOURCE", "http://localhost:8000/live")   # serial:COM5 | tcp://host:port | file:/path | http://.../live
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DEVICE_ID = os.getenv("DEVICE_ID", "haske-bms-001")
PUBLISH_INTERVAL_S = float(os.getenv("PUBLISH_INTERVAL_S", "5"))
CAPACITY_AH = float(os.getenv("CAPACITY_AH", "50"))

relay = Relay(open_source(SOURCE), MQTT_HOST, MQTT_PORT, DEVICE_ID, PUBLISH_INTERVAL_S, CAPACITY_AH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    relay.start()
    yield
    relay.stop()


app = FastAPI(title="WiFi gateway simulator", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "wifi-sim", "mqtt_connected": relay.connected,
            "wifi_up": relay.wifi_up, "source": SOURCE}


@app.get("/stats")
async def stats():
    return {**relay.stats, "last_error": relay.last_error, "cell_count": relay.cell_count}


@app.post("/wifi/{state}")
async def wifi(state: str):
    if state not in ("up", "down"):
        raise HTTPException(status_code=400, detail="use /wifi/up or /wifi/down")
    relay.set_wifi(state == "up")
    return {"wifi_up": relay.wifi_up}
```

</details>

Add `tests/test_source.py`, which checks the serial reader against a pseudo-terminal pair that stands in for a serial port:

```python
import os
import threading
import time

import pytest

from source import open_source
from test_thingset import NATIVE_SIM_LINE


def test_unknown_source_is_rejected():
    with pytest.raises(ValueError, match="serial:PORT"):
        open_source("ftp://nope")


@pytest.mark.skipif(os.name == "nt", reason="uses a Unix pseudo-terminal as a fake serial port")
def test_serial_source_reads_lines_from_a_port():
    master, slave = os.openpty()                       # a fake serial port pair
    port = os.ttyname(slave)

    def device():                                      # the "BMS" writes one noisy line, then a good one
        time.sleep(0.3)
        os.write(master, b"uart:~$ \r\n" + NATIVE_SIM_LINE.encode() + b"\r\n")

    threading.Thread(target=device, daemon=True).start()
    lines = open_source(f"serial:{port}@115200")
    got = [next(lines).strip() for _ in range(2)]
    assert got[1] == NATIVE_SIM_LINE
```

Run `uv run pytest -q`, expecting `24 passed`.

### 10.7 First run: simulator through the gateway

Three terminals, broker running (Part 2). Terminal A: the simulator from Part 9 **without its own MQTT** (port 8000), so that the gateway is the only publisher: `MQTT_ENABLED=false uv run uvicorn main:app --port 8000` (PowerShell: `$env:MQTT_ENABLED = "false"; ...`). Terminal B, from `services/wifi-sim`:

```bash
uv run uvicorn main:app --port 8002
```

PowerShell: `$env:DEVICE_ID = "haske-bms-001"; $env:SOURCE = "http://localhost:8000/live"; $env:PUBLISH_INTERVAL_S = "5"; uv run uvicorn main:app --port 8002`

Check:

```bash
curl localhost:8002/health
curl localhost:8002/stats
docker exec -it mosq mosquitto_sub -t "bms/haske-bms-001/#" -v
```

You should see `status online`, `info`, then one `meas` per interval and, on `bms/<id>/mpm`, one `first` message followed by silence until something changes (try `POST /scenario/overheat` on the simulator), and `/stats` with `bad_crc` 0, `no_cells` 0 and a growing `published`. In Home Assistant the device appears with its 20 entities (if you ran Part 8), published by the gateway. The *simulator* published nothing. Check `curl localhost:8000/health`: `"mqtt_enabled": false`.

Now two experiments:

1. **Cut the radio.** `curl -X POST localhost:8002/wifi/down`. The subscriber shows `offline` and the HA device becomes unavailable. The simulator keeps running, and the battery's state is *not* reset. `curl -X POST localhost:8002/wifi/up` brings it back with `online`.
2. **Garble the line.** Stop the simulator and run a tiny fake source instead: a script (or `python -m http.server` in a folder with a text file `live`) that serves a `#mLive` line whose digits you changed by hand. `/stats` shows `bad_crc` rising and nothing is published. The gateway protects the cloud from bad data.

### 10.8 Second run: the real firmware through the gateway

In WSL, with your `native_sim` build from Part 0 running (in a terminal) and its `uart_1 connected to pseudotty: /dev/pts/N` line noted:

```bash
stty -F /dev/pts/N raw -echo
```

Pick the way to carry the lines:

- **Gateway also in WSL**: run `SOURCE=file:/dev/pts/N uv run uvicorn main:app --port 8002` inside WSL. (Tested: this is how the 17-lines result was obtained.)
- **Gateway in Windows or Docker**: bridge the pseudo-terminal to a TCP port in WSL with `socat` (`sudo apt install socat`):

  ```bash
  socat TCP-LISTEN:4001,reuseaddr,fork FILE:/dev/pts/N,raw,echo=0
  ```

  and use `SOURCE=tcp://localhost:4001` (from Docker: `tcp://host.docker.internal:4001`). The TCP reader is tested, the `socat` command is not.

Then `curl localhost:8002/stats`. Expected: `lines` growing by one per second, `bad_crc: 0`, `no_cells` equal to `lines`, `published: 0`, and `last_error: "no cell connected (empty chip)"`. You now have the **real firmware's bytes flowing through your real gateway code**. What is missing is only the battery behind the chip.

### 10.9 Third run: the real C1 (when the hardware is available)

Nothing changes except one setting. Connect the BMS C1's UART through a USB-serial adapter (check the C1 manual for the connector and the 3.3 V logic level), find the port (`COMx` in the Windows Device Manager, `/dev/ttyUSB0` on Linux) and run:

```bash
SOURCE=serial:COM5 DEVICE_ID=haske-bms-001 uv run uvicorn main:app --port 8002
```

If `/stats` shows `bad_crc` growing, the baud rate or the wiring is wrong. If `no_cells` grows, the BMS is powered but has no cells connected. The MQTT side, Home Assistant and MPM do not change. This is why the gateway is built before the hardware arrives. (Not testable without the board.)

## Your turn

1. Make the gateway publish a retained `bms/<id>/gateway` topic with its `stats` (JSON) every 30 s. Why would operators want that? (Write the test first: `process_line` on a corrupted line increments `bad_crc`, which already exists.)
2. Add buffering: while `wifi_up` is false keep the last 100 messages in a `collections.deque(maxlen=100)` and send them in order after `up`. What does the `meas` message lack that a subscriber needs to place a late message correctly? (Hint: `ts` is set when the line is *processed*. Is that the time the measurement was *taken*?)
3. Make `source.py` detect a **silent source**: if no valid line has arrived for 15 s, `/health` should report `source_stale: true`. Which clock should it use?

<details>
<summary>Hints</summary>

1. Operators see a flood of CRC errors (a bad cable or baud rate) without looking at logs. Retained so a new dashboard gets it at once.
2. `ts` is taken at processing time, which for buffered messages is wrong unless you set it when the line is first received. Set it at receipt, not at send. The firmware itself sends no clock, so the gateway's clock is the only one.
3. A monotonic clock (`time.monotonic`), which cannot jump when the system time is adjusted. That is why `Relay` takes a `clock` parameter, which also lets a test use a fake one.

</details>

## Checkpoint

- `uv run pytest -q` in `services/wifi-sim` shows `24 passed`.
- The simulator's data arrives in MQTT through the gateway, and the radio on/off experiment works.
- You ran the gateway against the real `native_sim` and can explain why it published nothing.

## Commit

```bash
git add services/wifi-sim
git commit -m "feat(wifi-sim): serial #mLive to MQTT gateway simulator"
```

Next: [Part 11: Docker Compose](11-docker-compose.md).
