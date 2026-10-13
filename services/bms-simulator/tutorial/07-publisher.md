# Part 7: The MQTT publisher

**Goal.** Write `publisher.py`: a class that connects to the broker, announces the device, publishes a measurement message on a timer, and survives a broker restart without resetting the
battery. This is the code that makes the gateway see your simulator.

**Time.** 2 hours.

> **Production parity.** The publisher you write here is a *learning vehicle* and the code that the WiFi gateway (Part 10) reuses nearly unchanged. In the final stack the simulator runs with `MQTT_ENABLED=false` and the gateway publishes, so that nothing changes when a real BMS replaces the simulator. The topics and payloads below are the contract either way.

## Ideas

**The topic contract** (from the team's `docs/mqtt-schema.md`; `<id>` is the device id, for example `haske-bms-001`):

| Topic | Content | Retained | QoS | Why |
| --- | --- | --- | --- | --- |
| `bms/<id>/status` | `online` / `offline` | yes | 1 | A newcomer must learn at once whether the device is up. `offline` is the last will. |
| `bms/<id>/info` | static device facts and limits | yes | 1 | Rarely changes; every new subscriber should get it immediately. |
| `bms/<id>/meas` | the full measurement, every few seconds. **Home Assistant** reads this | no | 0 | The next one replaces it in seconds. A retained old reading would look current when it is not. |
| `bms/<id>/mpm` | a compact summary, only when something is worth a decision (7.6). **MPM** reads this | no | 1 | MPM acts on decisions, not on a stream. QoS 1 because a missed fault event is *not* replaced seconds later. |

**Two topics, two audiences.** Home Assistant draws graphs, so it wants the full picture often (set `PUBLISH_INTERVAL_S=1` if you like). MPM makes decisions (schedule a service, flag a battery), so it wants a short message when the state of the battery changes, plus a slow heartbeat, not every second of the stream. The device never decides who is interested: it publishes both, each consumer subscribes to its own topic.

**One class, two threads.** paho runs the network in a background thread (`loop_start`). You add your own thread that steps the battery and publishes on a timer. Both
use the same client. paho's `publish()` is thread-safe.

**Reconnect.** `connect_async()` returns immediately and keeps trying in the background even if the broker is down *right now*. The service therefore starts without
the broker, which makes `docker compose up` order-independent. The `on_connect` callback runs on **every** (re)connect, so retained topics are restored after a broker restart.

**The battery lives on.** The simulation thread steps the model *whether or not* the broker is connected. A broker outage must not reset or pause the battery. It simply skips publishing.

**Validate before sending.** `Meas.model_validate(...)` guarantees that the payload obeys the contract. If the model has a bug, the exception is raised in your code and the broker never sees a bad message.

**Speed.** `SPEED` is simulated seconds per real second. With `PUBLISH_INTERVAL_S=5` and `SPEED=60` each published sample is five real seconds apart and covers five simulated minutes. A simulated day passes in 24 real minutes.

## Steps

### 7.1 Tests that need no broker

`tests/test_publisher.py`:

```python
"""The parts of the publisher that need no broker."""
import json

from publisher import Publisher
from schema import Info, Meas
from sim import Simulator


def make() -> Publisher:
    # Creating a Publisher does not connect: connecting happens in start().
    return Publisher(Simulator(seed=1), "localhost", 1883, "dev1", interval_s=1.0, speed=60.0)


def test_topics_start_with_the_device_base():
    assert make().base == "bms/dev1"


def test_meas_json_is_valid_and_complete():
    pub = make()
    msg = json.loads(pub._meas_json())
    assert msg["schema"] == 1 and msg["device_id"] == "dev1"
    assert msg["ts"].endswith("Z")
    Meas.model_validate(msg)


def test_info_json_carries_the_limits_and_cell_count():
    info = Info.model_validate(json.loads(make()._info_json()))
    assert info.cell_count == 8
    assert info.limits["cell_overvoltage_v"] == 3.8


def test_not_connected_at_the_start():
    assert make().connected is False
```

These test what *can* be tested without a network: topic names, payload validity, and that creating the object does not connect.
The network behaviour you will check by eye in 7.4. (A fuller test setup could use a throw-away broker in Docker, a good stretch goal.)

### 7.2 Skeleton

```python
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from schema import Info, Meas
from sim import Simulator

log = logging.getLogger("publisher")
SCHEMA_VERSION = 1


class Publisher:
    def __init__(self, model: Simulator, host: str, port: int, device_id: str,
                 interval_s: float, speed: float):
        ...

    # lifecycle
    def start(self) -> None: ...
    def stop(self) -> None: ...

    # callbacks (called by paho's network thread)
    def _on_connect(self, client, userdata, flags, reason_code, properties=None): ...
    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None): ...

    # payloads
    def _info_json(self) -> str: ...
    def meas_dict(self) -> dict: ...
    def _meas_json(self) -> str: ...

    # the timer thread
    def _loop(self) -> None: ...
```

### 7.3 What goes where

**`__init__`.** Store the arguments. `self.base = f"bms/{device_id}"`. State: `self.connected = False`, `self.published = 0`, a `threading.Event()` called `_stop`, and a daemon `threading.Thread(target=self._loop, name="bms-publisher", daemon=True)`.
Create the client with `mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{device_id}")`. Then:

- `client.will_set(f"{self.base}/status", "offline", qos=1, retain=True)`, the last will, **registered before connecting**.
- `client.reconnect_delay_set(min_delay=1, max_delay=30)`.
- attach `on_connect` and `on_disconnect`.

**`start`.** `client.connect_async(host, port, keepalive=30)`, `client.loop_start()`, `self._thread.start()`. The 30-second keepalive bounds how long the broker takes to notice a silent death (about 45 s).

**`stop`.** `self._stop.set()`. If connected, publish `offline` (retained, QoS 1) and wait for it (`info = client.publish(...)`, then `info.wait_for_publish(timeout=2)`). A *clean* shutdown never triggers the last will, so we say goodbye ourselves.
Then `loop_stop()` and `disconnect()`.

**`_on_connect`.** If `reason_code != 0` log an error and return. Otherwise set `connected = True`, and publish **retained, QoS 1**: `status` = `online`, then `info`. (In Part 8 you add the discovery messages here.)
Reason codes in paho 2 are objects; `reason_code != 0` works for "not Success".

**`_on_disconnect`.** `connected = False`, log a warning. Do not reconnect by hand: paho does it.

**`_info_json`.** Build an `Info(...)` with `schema=SCHEMA_VERSION` (via the alias), the device id, manufacturer `"Libre Solar"`, model `"BMS C1 (simulated)"`, firmware `"simulator-0.1"`, chemistry `"LFP"`, cell count and
capacity from the model, and `limits=self.model.limits()`. Return `info.model_dump_json(by_alias=True)`, because **by_alias** makes the key `schema` instead of `schema_version`.

**`meas_dict`.** `{"schema": 1, "device_id": ..., "ts": <UTC time as 2026-10-13T08:30:00Z>, **self.model.snapshot()}`. For the time: `datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`. Always use timezone-aware UTC.

**`_meas_json`.** `Meas.model_validate(self.meas_dict()).model_dump_json(by_alias=True)`.

**`_loop`.** While not `_stop.is_set()`: step the model by `interval_s * speed` simulated seconds; if connected, publish `_meas_json()` to `bms/<id>/meas` with QoS 0 and count it; then `self._stop.wait(self.interval_s)`.
`Event.wait(t)` is a `sleep` that returns immediately when `stop()` is called, so shutdown is not delayed by up to a whole interval.

<details>
<summary>Solution: publisher.py (leave out the two lines marked "Part 8" for now)</summary>

```python
"""MQTT publisher: connects, announces the device, then streams measurements."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from discovery import discovery_messages          # added in Part 8
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
```

</details>

Run the tests: `uv run pytest tests/test_publisher.py -q` should say `4 passed`.

### 7.4 Try it by hand (no web server yet)

With the broker from Part 2 running, create a throw-away script `dev_publish.py`:

```python
import logging
import time

from publisher import Publisher
from sim import Simulator

logging.basicConfig(level=logging.INFO)
pub = Publisher(Simulator(seed=1), "localhost", 1883, "haske-bms-001", interval_s=1.0, speed=300.0)
pub.start()
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pub.stop()
```

In another terminal watch everything the device says:

```bash
docker exec -it mosq mosquitto_sub -t "bms/#" -v
```

Run the script (`uv run python dev_publish.py`). You should see, first, two retained messages and then one `meas` per second:

```text
bms/haske-bms-001/status online
bms/haske-bms-001/info {"schema":1,"device_id":"haske-bms-001","manufacturer":"Libre Solar", ... }
bms/haske-bms-001/meas {"schema":1,"device_id":"haske-bms-001","ts":"2026-10-13T08:30:01Z","state":"NORMAL", ... }
```

### 7.5 Break it on purpose

These are the experiments that prove the design. Do all four and write what you observed.

1. **Ctrl+C the script.** The status becomes `offline` (our clean goodbye).
2. **Kill the script hard.** Start it again, then close the terminal window or kill the process. After about 45 seconds the broker publishes `offline` itself: that is the last will.
3. **Restart the broker** while the script runs (Ctrl+C in the broker's terminal and start it again). The script logs a disconnect, reconnects within a few seconds and republishes `online` and `info`. Look at `soc_pct`: it **did not reset**.
4. **Start the script before the broker.** It waits, and publishes when the broker appears.

<details>
<summary>What you should see</summary>

1. `bms/haske-bms-001/status offline` immediately.
2. The same message, but about 45 seconds later (1.5 x the keepalive of 30 s). It can differ a little depending on the platform.
3. `disconnected from the broker (...); paho will retry`, then `connected to localhost:1883`. `soc_pct` continues from where it was, because the model thread never stopped.
4. paho retries with a growing delay (1 s up to 30 s), then connects. This is why the compose file in Part 11 does not strictly need the broker to be ready first.

</details>

### 7.6 A second topic for MPM

`meas` is right for Home Assistant and wrong for MPM: it is a stream, and a decision system drowns in streams. So the publisher gets a second, much quieter topic, `bms/<id>/mpm`. A small class, `MpmFilter`, sees **every** measurement and lets through only these moments:

| `reason` | When |
| --- | --- |
| `first` | the first measurement after start (so MPM learns the starting point) |
| `state_change` | `OFF`, `CHG`, `DIS`, `NORMAL` or `SHUTDOWN` changed |
| `errors_changed` | a fault appeared or cleared |
| `imbalance_high` / `imbalance_ok` | the spread between the highest and lowest cell crossed 50 mV, in either direction |
| `soc_step` | the charge moved 5 points away from what MPM last heard |
| `heartbeat` | nothing else happened for 300 s: proof of life, and MPM's "last seen" |

The payload is a summary (state, SoC, voltage, current, `imbalance_mv`, `max_temp_c`, `errors`), without the per-cell arrays. `imbalance_mv` and `max_temp_c` are plain arithmetic on fields the firmware already reports. Nothing is invented (see the production-parity rules in the README).

The filter has no MQTT and no clock inside (you pass `now`), which is why it is easy to test. Write `tests/test_mpm.py` first:

```python
"""The MPM filter: pure logic, no broker, no clock (we pass `now` ourselves)."""
from mpm import MpmEvent, MpmFilter


def meas(**kw) -> dict:
    base = {"schema": 1, "device_id": "dev1", "ts": "2026-10-13T08:30:00Z", "state": "NORMAL",
            "pack_voltage_v": 26.1, "pack_current_a": 3.0, "soc_pct": 60.0,
            "cell_voltages_v": [3.27, 3.27, 3.28, 3.27], "cell_temps_c": [25.0, 26.0],
            "mosfet_temp_c": 27.0, "balancing_mask": 0, "errors": []}
    return {**base, **kw}


def test_first_measurement_is_sent_then_a_steady_stream_is_silent():
    f = MpmFilter()
    assert f.check(meas(), 0)["reason"] == "first"
    assert [f.check(meas(), t) for t in range(1, 60)] == [None] * 59


def test_a_fault_appearing_or_clearing_is_sent():
    f = MpmFilter()
    f.check(meas(), 0)
    assert f.check(meas(errors=["CHG_OVERTEMP"]), 1)["reason"] == "errors_changed"
    assert f.check(meas(errors=["CHG_OVERTEMP"]), 2) is None          # same fault: nothing new
    assert f.check(meas(errors=[]), 3)["reason"] == "errors_changed"


def test_state_change_and_soc_step():
    f = MpmFilter(soc_step_pct=5.0)
    f.check(meas(), 0)
    assert f.check(meas(soc_pct=63.0), 1) is None                     # small drift is not a decision
    assert f.check(meas(soc_pct=65.5), 2)["reason"] == "soc_step"     # 5.5 away from the 60 MPM heard
    assert f.check(meas(soc_pct=65.5, state="CHG"), 3)["reason"] == "state_change"


def test_imbalance_is_sent_when_it_crosses_the_limit_in_either_direction():
    f = MpmFilter(imbalance_mv=50.0)
    f.check(meas(), 0)
    bad = meas(cell_voltages_v=[3.20, 3.27, 3.28, 3.27])              # 80 mV spread
    assert f.check(bad, 1)["reason"] == "imbalance_high"
    assert f.check(bad, 2) is None
    assert f.check(meas(), 3)["reason"] == "imbalance_ok"


def test_heartbeat_when_nothing_else_happens():
    f = MpmFilter(heartbeat_s=300.0)
    f.check(meas(), 0)
    assert f.check(meas(), 299) is None
    assert f.check(meas(), 300)["reason"] == "heartbeat"
    assert f.check(meas(), 301) is None                               # the timer restarted


def test_the_event_is_compact_and_valid():
    event = MpmFilter().check(meas(errors=["CHG_OVERTEMP"]), 0)
    MpmEvent.model_validate(event)
    assert event["imbalance_mv"] == 10.0 and event["max_temp_c"] == 27.0
    assert "cell_voltages_v" not in event and "cell_temps_c" not in event
```

Then `mpm.py`. The thresholds are constructor arguments with the defaults above.

<details>
<summary>Solution: mpm.py</summary>

```python
"""Decision-worthy summaries for MPM (topic bms/<id>/mpm). No MQTT in here: pure logic, easy to test."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schema import ErrorName

SCHEMA_VERSION = 1
Reason = Literal["first", "state_change", "errors_changed", "imbalance_high", "imbalance_ok",
                 "soc_step", "heartbeat"]


class MpmEvent(BaseModel):
    """Payload of bms/<id>/mpm. Small on purpose: no per-cell arrays."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(alias="schema")
    device_id: str
    ts: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    reason: Reason
    state: Literal["OFF", "CHG", "DIS", "NORMAL", "SHUTDOWN"]
    soc_pct: float = Field(ge=0, le=100)
    pack_voltage_v: float
    pack_current_a: float
    imbalance_mv: float
    max_temp_c: float
    errors: list[ErrorName]


class MpmFilter:
    """Looks at EVERY measurement, lets through only the ones worth a decision."""

    def __init__(self, soc_step_pct: float = 5.0, imbalance_mv: float = 50.0, heartbeat_s: float = 300.0):
        self.soc_step_pct, self.imbalance_mv, self.heartbeat_s = soc_step_pct, imbalance_mv, heartbeat_s
        self._last: dict | None = None      # what MPM was last told
        self._last_at = 0.0

    def check(self, meas: dict, now: float) -> dict | None:
        """Return the event to publish (a dict that obeys MpmEvent), or None. `now` is a monotonic clock."""
        v = meas["cell_voltages_v"]
        imbalance = round((max(v) - min(v)) * 1000.0, 1)
        errors = sorted(meas["errors"])
        high = imbalance >= self.imbalance_mv
        last = self._last

        if last is None:
            reason = "first"
        elif meas["state"] != last["state"]:
            reason = "state_change"
        elif errors != last["errors"]:
            reason = "errors_changed"
        elif high != last["high"]:
            reason = "imbalance_high" if high else "imbalance_ok"
        elif abs(meas["soc_pct"] - last["soc_pct"]) >= self.soc_step_pct:
            reason = "soc_step"            # measured against what MPM last heard, so no flapping at a boundary
        elif now - self._last_at >= self.heartbeat_s:
            reason = "heartbeat"           # proof of life, and MPM's "last seen"
        else:
            return None

        self._last = {"state": meas["state"], "errors": errors, "high": high, "soc_pct": meas["soc_pct"]}
        self._last_at = now
        event = MpmEvent(
            schema=SCHEMA_VERSION, device_id=meas["device_id"], ts=meas["ts"], reason=reason,
            state=meas["state"], soc_pct=meas["soc_pct"], pack_voltage_v=meas["pack_voltage_v"],
            pack_current_a=meas["pack_current_a"], imbalance_mv=imbalance,
            max_temp_c=max([*meas["cell_temps_c"], meas["mosfet_temp_c"]]), errors=errors,
        )
        return event.model_dump(by_alias=True)
```

</details>

The wiring is already in the publisher solution above (the lines marked "7.6"): after each `meas`, `_publish_mpm()` asks the filter and publishes to `bms/<id>/mpm` with QoS 1, **not retained** (an old event must not look current; the retained `status` already tells a newcomer whether the device is up).

Run `uv run pytest tests/test_mpm.py -q`; expect `6 passed`. Then watch both topics side by side:

```bash
docker exec -it mosq mosquitto_sub -t "bms/+/meas" -t "bms/+/mpm" -v
```

With `dev_publish.py` you see a `meas` every second and, after the first `mpm`, silence. Trigger something (`POST /scenario/overheat` once Part 9 exists) and an `mpm` message with `"reason":"errors_changed"` appears within one sample.

## Checkpoint

- `uv run pytest -q` is green: `59 passed`.
- `mosquitto_sub` shows status, info and meas, and the four experiments behave as described.

## Commit

```bash
git add services/bms-simulator
git commit -m "feat(bms-simulator): MQTT publisher with LWT, retained status/info and reconnect"
```

## Self-check questions

1. Why is `meas` not retained but `status` is?
2. What would happen to a subscriber that connects at 12:00 if `meas` were retained and the simulator had died at 09:00?
3. Why `connect_async` and not `connect`?
4. Why is `mpm` QoS 1 but `meas` QoS 0, and why are neither retained?

<details>
<summary>Answers</summary>

1. A reading is replaced seconds later, so an old retained one would only mislead. Status is a state that stays true until it changes, and a newcomer needs it at once.
2. It would receive a 09:00 reading that looks current. Retained data has no expiry unless the consumer adds one.
3. `connect` raises an error if the broker is down at startup. `connect_async` returns at once and lets the background loop keep trying, so start order does not matter.
4. A lost `meas` is replaced a second later; a lost fault event is not, so `mpm` gets the stronger guarantee. Neither is retained because both are events about a moment, and a late subscriber must not mistake an old one for the present.

</details>

Next: [Part 8: Home Assistant](08-home-assistant.md).
