# Part 6: Glue and mapping

**Goal.** Connect the battery and the firmware into one object, `Simulator`, and translate the firmware's `Meas` items into the team's MQTT `meas` message
(`docs/mqtt-schema.md`). Afterwards a single call, `sim.step(seconds)`, runs the whole closed loop and you can print what the BMS believes next to the truth.

**Time.** 1.5 hours.

## Ideas

**The control loop.** In Part 4 the battery took the MOSFET switch states as input. In Part 5 the firmware produced them. Now close the loop. Every iteration:

```text
reading = battery.step(dt, firmware.chg_on, firmware.dis_on, firmware.balancing_mask)   # physics runs
firmware.update(reading, now_ms)                                                         # firmware reacts
```

Open switches stop the current, which changes the next reading, which changes the flags. This feedback is what you saw in the real device and why the model needs both halves.

**Chunking.** A request such as "advance 3600 s" is split into steps of at most 30 s, so that heating, charge and protections are evaluated often enough. (The real firmware polls every 100 ms. We trade accuracy for speed.)

**Threads.** In Part 7 the publisher thread calls `step()` while the web API thread calls `snapshot()`. Two threads touching the same lists at once can produce half-updated results. A `threading.Lock` makes one thread wait while the other
is inside a `with self._lock:` block.

**A contract as code.** The schema in `docs/mqtt-schema.md` is prose. `schema.py` turns it into **pydantic** models that *validate*: before a message is published, the publisher checks it. A bug in the simulator can then never put a wrong message on the broker.
(One gotcha: `schema` is a reserved name inside pydantic models, so the Python attribute is `schema_version` and an *alias* keeps the JSON key `"schema"`.)

**Mapping.** The ThingSet world and the MQTT world differ in names, units, structure, and in what they include:

| MQTT field | ThingSet item | Transformation |
| --- | --- | --- |
| `state` | `rBmsState` | number to name |
| `pack_voltage_v` | `rPackVoltage_V` | rounded to 2 decimals |
| `pack_current_a` | `rPackCurrent_A` | rounded to 2 decimals, positive = charging |
| `soc_pct` | `rSOC_pct` | rounded to 1 decimal |
| `cell_voltages_v` | `rCellVoltages_V` | drop the unconnected channels |
| `cell_temps_c` | `rCellTemps_degC` | rounded to 1 decimal |
| `mosfet_temp_c` | `rMOSFETTemp_degC` | rounded to 1 decimal |
| `balancing_mask` | `rBalancingStatus` | as is |
| `errors` | `rErrorFlags` | bit mask to a list of names |
| `schema`, `device_id`, `ts` | (none) | added by the publisher: the firmware sends no clock and no id |

## Steps

### 6.1 Tests first

`tests/test_mapping.py`:

```python
from chip import ChipReading
from firmware import ERR, Firmware
from mapping import error_names, to_mqtt
from schema import Meas


def test_error_names_decode_the_bit_mask():
    assert error_names(0) == []
    assert error_names(ERR["CELL_UNDERVOLTAGE"] | ERR["CHG_OVERTEMP"]) == ["CELL_UNDERVOLTAGE", "CHG_OVERTEMP"]


def test_unconnected_channels_are_dropped_and_the_result_matches_the_schema():
    cells = [3.31, 3.30, 3.32] + [0.0] * 13
    r = ChipReading(cells, 4.2, [25.0, 24.5], 27.0, 26.0)
    fw = Firmware()
    fw.boot(r)
    fw.update(r, 1000)
    msg = to_mqtt(fw.meas())
    assert msg["cell_voltages_v"] == [3.31, 3.3, 3.32]
    assert msg["state"] in {"DIS", "NORMAL"}
    Meas.model_validate({"schema": 1, "device_id": "t", "ts": "2026-01-01T00:00:00Z", **msg})
```

`tests/test_sim.py`:

```python
import pytest

from battery import SCENARIOS, ocv
from schema import Meas
from sim import Simulator
from thingset import decode_live

HOUR = 3600
DAY = 24 * HOUR


def run(sim: Simulator, seconds: float, dt: float = 600.0):
    t = 0.0
    while t < seconds:
        sim.step(dt)
        t += dt
        yield sim.snapshot()


def as_meas(snap: dict) -> Meas:
    return Meas.model_validate({"schema": 1, "device_id": "t", "ts": "2026-01-01T00:00:00Z", **snap})


def test_ocv_is_flat_in_the_middle_and_collapses_below_zero():
    assert ocv(0.5) == pytest.approx(3.265, abs=0.005)
    assert ocv(1.0) == pytest.approx(3.392)
    assert ocv(-0.05) < 2.5


def test_same_seed_gives_identical_runs():
    a = [s["soc_pct"] for s in run(Simulator(seed=7), DAY)]
    b = [s["soc_pct"] for s in run(Simulator(seed=7), DAY)]
    assert a == b


def test_normal_week_raises_no_fault_and_stays_normal():
    sim = Simulator(seed=1)
    for snap in run(sim, 7 * DAY):
        assert snap["errors"] == []
        assert snap["state"] == "NORMAL"
        assert 0.0 <= snap["soc_pct"] <= 100.0
        assert 20.0 < max(snap["cell_temps_c"]) < 40.0


def test_every_snapshot_matches_the_schema_in_every_scenario():
    for scenario in SCENARIOS:
        for snap in run(Simulator(seed=2, scenario=scenario), DAY):
            as_meas(snap)


def test_the_live_line_always_has_a_valid_crc_and_agrees_with_the_snapshot():
    sim = Simulator(seed=3)
    sim.step(5000)
    meas = decode_live(sim.live_line())            # raises on a bad CRC
    assert meas["rSOC_pct"] == pytest.approx(sim.snapshot()["soc_pct"], abs=0.05)
    assert len(meas["rCellVoltages_V"]) == 16


def test_charging_raises_soc_and_discharging_lowers_it():
    sim = Simulator(seed=1, start_hour=9.0)
    before = sim.snapshot()["soc_pct"]
    sim.step(HOUR)
    assert sim.snapshot()["soc_pct"] > before
    sim = Simulator(seed=1, start_hour=19.0)
    before = sim.snapshot()["soc_pct"]
    sim.step(HOUR)
    assert sim.snapshot()["soc_pct"] < before


def test_firmware_soc_is_an_estimate_not_the_truth():
    sim = Simulator(seed=1)
    list(run(sim, 2 * DAY))
    err = sim.debug()["soc_estimate_error_pct"]
    assert err != 0.0 and abs(err) < 10.0


def test_balancing_eventually_evens_out_the_cells():
    sim = Simulator(seed=1, start_hour=9.0)
    spread0 = None
    for snap in run(sim, 12 * DAY):
        v = snap["cell_voltages_v"]
        if spread0 is None:
            spread0 = max(v) - min(v)
    v = sim.snapshot()["cell_voltages_v"]
    assert max(v) - min(v) < 0.010


def test_cell_drift_ends_in_a_cell_failure_within_a_day():
    sim = Simulator(seed=1, scenario="cell_drift")
    seen = set()
    for snap in run(sim, DAY, dt=60.0):
        seen.update(snap["errors"])
    assert "CELL_FAILURE" in seen
    assert sim.snapshot()["state"] == "OFF"


def test_overheat_trips_the_temperature_flags_and_opens_the_switches():
    sim = Simulator(seed=1, scenario="overheat")
    seen = set()
    for snap in run(sim, 4 * HOUR, dt=60.0):
        seen.update(snap["errors"])
    assert {"CHG_OVERTEMP", "DIS_OVERTEMP"} <= seen
    assert sim.snapshot()["state"] == "OFF"


def test_switching_back_to_normal_restores_the_cells_but_a_cooled_pack_recovers():
    sim = Simulator(seed=1, scenario="overheat")
    list(run(sim, 3 * HOUR, dt=60.0))
    sim.set_scenario("normal")
    list(run(sim, 6 * HOUR, dt=60.0))
    assert sim.snapshot()["errors"] == []


def test_unknown_scenario_is_rejected():
    with pytest.raises(ValueError):
        Simulator().set_scenario("bogus")
```

Skim `test_sim.py`. These are *behaviour* tests of the whole system: a normal week raises no fault, the live line always has a valid CRC, balancing evens out the cells within twelve days, `cell_drift` ends in `CELL_FAILURE` within a day, `overheat` opens the switches.

### 6.2 `mapping.py`

Two functions, using what you built in Part 5:

```python
def error_names(flags: int) -> list[str]:
    """[ 'CELL_UNDERVOLTAGE', ...] for every bit set in the mask, lowest bit first."""

def to_mqtt(meas: dict) -> dict:
    """Same keys as the schema, minus schema, device_id and ts."""
```

Hints: `for bit, name in enumerate(ERROR_BITS)` and `flags >> bit & 1`. `STATE_NAMES[meas["rBmsState"]]` gives the name. In `to_mqtt` round with the decimals of the table above.

<details>
<summary>Solution: mapping.py</summary>

```python
"""ThingSet `Meas` items  ->  the team's MQTT `meas` message (docs/mqtt-schema.md)."""
from __future__ import annotations

from chip import NOT_CONNECTED_V
from firmware import ERROR_BITS, STATE_NAMES


def error_names(flags: int) -> list[str]:
    return [name for bit, name in enumerate(ERROR_BITS) if flags >> bit & 1]


def to_mqtt(meas: dict) -> dict:
    """Same keys as the schema, minus `schema`, `device_id` and `ts` (the publisher adds those)."""
    return {
        "state": STATE_NAMES[meas["rBmsState"]],
        "pack_voltage_v": round(meas["rPackVoltage_V"], 2),
        "pack_current_a": round(meas["rPackCurrent_A"], 2),
        "soc_pct": round(meas["rSOC_pct"], 1),
        "cell_voltages_v": [round(v, 3) for v in meas["rCellVoltages_V"] if v > NOT_CONNECTED_V],
        "cell_temps_c": [round(t, 1) for t in meas["rCellTemps_degC"]],
        "mosfet_temp_c": round(meas["rMOSFETTemp_degC"], 1),
        "balancing_mask": meas["rBalancingStatus"],
        "errors": error_names(meas["rErrorFlags"]),
    }
```

</details>

### 6.3 `schema.py`

Type this one in from the solution (it is data definition, not logic), and read it as you type. Notice `Literal[...]` (only these exact values are allowed), `Field(ge=0, le=100)` (range check), `pattern=` (regular expression for the timestamp), and `extra="forbid"`
(an unknown field is an error, so typos are caught).

<details>
<summary>schema.py</summary>

```python
"""The data contract, as code. Mirrors docs/mqtt-schema.md (schema version 1)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ErrorName = Literal[
    "CELL_UNDERVOLTAGE", "CELL_OVERVOLTAGE", "SHORT_CIRCUIT", "DIS_OVERCURRENT",
    "CHG_OVERCURRENT", "OPEN_WIRE", "DIS_UNDERTEMP", "DIS_OVERTEMP", "CHG_UNDERTEMP",
    "CHG_OVERTEMP", "INT_OVERTEMP", "CELL_FAILURE", "DIS_OFF", "CHG_OFF", "FET_OVERTEMP",
]


class Meas(BaseModel):
    """Payload of bms/<id>/meas."""

    # The JSON key is "schema", but `schema` is a reserved name inside pydantic models,
    # so the Python attribute is schema_version and the alias keeps the JSON key.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(alias="schema")
    device_id: str = Field(min_length=1)
    ts: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    state: Literal["OFF", "CHG", "DIS", "NORMAL", "SHUTDOWN"]
    pack_voltage_v: float
    pack_current_a: float
    soc_pct: float = Field(ge=0, le=100)
    cell_voltages_v: list[float] = Field(min_length=1)
    cell_temps_c: list[float] = Field(min_length=1)
    mosfet_temp_c: float
    balancing_mask: int = Field(ge=0)
    errors: list[ErrorName]


class Info(BaseModel):
    """Payload of bms/<id>/info."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(alias="schema")
    device_id: str
    manufacturer: str
    model: str
    firmware: str
    chemistry: str
    cell_count: int = Field(gt=0)
    capacity_ah: float = Field(gt=0)
    limits: dict[str, float]
```

</details>

Compare it with `docs/mqtt-schema.md` on the `feat/mqtt-simulator-ha` branch (`git show feat/mqtt-simulator-ha:docs/mqtt-schema.md`). Every field there must be here. If you find a mismatch, tell your teammate. That is the kind of bug this file exists to find.

### 6.4 `sim.py`

```python
from __future__ import annotations

import threading

import mapping
import thingset
from battery import Battery
from firmware import Config, Firmware

MAX_CHUNK_S = 30.0     # one firmware loop iteration per at most 30 simulated seconds


class Simulator:
    def __init__(self, cells: int = 8, capacity_ah: float = 50.0, start_hour: float = 8.0,
                 seed: int | None = None, scenario: str = "normal"):
        ...
    def set_scenario(self, name: str) -> None: ...
    @property
    def scenario(self) -> str: ...
    def step(self, dt_sim_s: float) -> None: ...
    def live_line(self) -> str: ...
    def snapshot(self) -> dict: ...
    def limits(self) -> dict[str, float]: ...
    def debug(self) -> dict: ...
```

What each does:

- `__init__`: create the `Battery(cells, capacity_ah, start_hour=..., seed=...)` and `Firmware(Config(capacity_ah=capacity_ah))`; store `n`, `capacity_ah`, a `threading.Lock()` and `now_ms = 0.0`. Then **boot** the firmware with the battery's first reading (`fw.boot(battery.read_chip())`) and select the scenario.
- `step(dt_sim_s)`: `with self._lock:` loop while time remains; take `chunk = min(MAX_CHUNK_S, remaining)`, advance the battery one chunk (pass the firmware's `chg_on`, `dis_on`, `balancing_mask`), advance `now_ms`, call `fw.update(reading, now_ms)`.
- `live_line()`: `thingset.encode_live(self.fw.meas())` under the lock. This is "what the serial port would carry".
- `snapshot()`: `mapping.to_mqtt(self.fw.meas())` under the lock. This is the MQTT message body.
- `limits()`: a dict of the thresholds the `info` message publishes (see the solution for the keys. They come from the firmware `Config`).
- `debug()`: values that exist only in the simulation (not on a real BMS): the scenario, the hour of day, the ambient temperature, the **true** State of Charge (the mean of the battery's cell charges, in percent) and the firmware's **estimate error** (`fw.soc - true_soc`). These feed the `/state` endpoint in Part 9 and are invaluable for understanding the estimate.

<details>
<summary>Solution: sim.py</summary>

```python
"""Battery + firmware, glued together.  This is the object the publisher and the API talk to."""
from __future__ import annotations

import threading

import firmware as fwmod
import mapping
import thingset
from battery import SCENARIOS, Battery
from firmware import Config, Firmware

MAX_CHUNK_S = 30.0     # one firmware loop iteration per <= 30 simulated seconds


class Simulator:
    def __init__(self, cells: int = 8, capacity_ah: float = 50.0, start_hour: float = 8.0,
                 seed: int | None = None, scenario: str = "normal"):
        self.battery = Battery(cells, capacity_ah, start_hour=start_hour, seed=seed)
        self.fw = Firmware(Config(capacity_ah=capacity_ah))
        self.n = cells
        self.capacity_ah = capacity_ah
        self._lock = threading.Lock()          # publisher thread and web API share this object
        self.now_ms = 0.0
        self.fw.boot(self.battery.read_chip())
        self.set_scenario(scenario)

    def set_scenario(self, name: str) -> None:
        with self._lock:
            self.battery.set_scenario(name)

    @property
    def scenario(self) -> str:
        return self.battery.scenario

    def step(self, dt_sim_s: float) -> None:
        with self._lock:
            while dt_sim_s > 0:
                chunk = min(MAX_CHUNK_S, dt_sim_s)
                reading = self.battery.step(chunk, self.fw.chg_on, self.fw.dis_on,
                                            self.fw.balancing_mask)
                self.now_ms += chunk * 1000.0
                self.fw.update(reading, self.now_ms)
                dt_sim_s -= chunk

    # ------------------------------------------------------------------ outputs
    def live_line(self) -> str:
        """The line the real firmware would send on its serial port."""
        with self._lock:
            return thingset.encode_live(self.fw.meas())

    def snapshot(self) -> dict:
        """The MQTT `meas` message body."""
        with self._lock:
            return mapping.to_mqtt(self.fw.meas())

    @staticmethod
    def limits_for(cfg: Config) -> dict[str, float]:
        return {
            "cell_overvoltage_v": cfg.cell_ov, "cell_undervoltage_v": cfg.cell_uv,
            "chg_max_temp_c": cfg.chg_ot, "dis_max_temp_c": cfg.dis_ot,
            "temp_hysteresis_c": cfg.temp_hyst, "cell_failure_diff_v": cfg.cell_failure_diff_v,
            "chg_overcurrent_a": cfg.chg_oc, "dis_overcurrent_a": cfg.dis_oc,
        }

    def limits(self) -> dict[str, float]:
        return self.limits_for(self.fw.cfg)

    def debug(self) -> dict:
        """Values that only exist inside the simulation (the BMS does not know them)."""
        with self._lock:
            b = self.battery
            true_soc = 100.0 * sum(b.soc) / b.n
            return {
                "scenario": b.scenario,
                "sim_hour": round(b.hour_of_day(), 2),
                "ambient_c": round(b.ambient_c, 1),
                "true_soc_pct": round(true_soc, 1),
                "soc_estimate_error_pct": round(self.fw.soc - true_soc, 1),
            }
```

</details>

### 6.5 Run

```bash
uv run pytest -q
```

Expected: `49 passed` (your health test, plus 4 + 5 + 13 + 11 + 1 from Parts 3 to 5, plus 2 mapping and 12 simulator tests). The
`test_sim.py` tests take about two seconds, since they simulate whole days.

## Look at the closed loop

`dev_firmware.py`:

```python
"""Part 5/6: the firmware's belief versus the truth, one line per 6 hours for 3 days."""
from sim import Simulator

sim = Simulator(seed=1)
print("  day  hour  state   soc_fw%  soc_true%  flags")
for step in range(3 * 4):
    sim.step(6 * 3600)
    snap, dbg = sim.snapshot(), sim.debug()
    print(f"{step * 6 // 24 + 1:5d} {dbg['sim_hour']:5.1f}  {snap['state']:7} {snap['soc_pct']:7.1f}"
          f" {dbg['true_soc_pct']:10.1f}  {snap['errors'] or '-'}")
```

```bash
uv run python dev_firmware.py
```

Reference result:

```text
  day  hour  state   soc_fw%  soc_true%  flags
    1  14.0  NORMAL     96.4       98.0  -
    1  20.0  NORMAL     74.6       74.8  -
    1   2.0  NORMAL     54.9       54.1  -
    1   8.0  NORMAL     58.4       57.7  -
    2  14.0  NORMAL     97.0       98.2  -
    2  20.0  NORMAL     75.2       75.0  -
    2   2.0  NORMAL     55.5       54.3  -
    2   8.0  NORMAL     58.9       57.9  -
    3  14.0  NORMAL     97.6       98.5  -
    3  20.0  NORMAL     75.9       75.3  -
    3   2.0  NORMAL     56.4       54.9  -
    3   8.0  NORMAL     59.7       58.4  -
```

Read it as a story. The firmware's estimate (`soc_fw%`) is always within a couple of points of the truth, but never exactly right, and its error is not constant. At 14:00 the pack is full, yet the
estimate says 96 to 97 %: the fullest cell is at 100 % and the others are lower, and the firmware looks at the average. By night the estimate is about 0.7 points *above* the truth. The error has
swung by about 2 points during the evening discharge (roughly 44 points of discharge times the 5 % capacity mismatch): the firmware divides the counted charge by the nominal 50 Ah while the cells really
hold 47.5 Ah, so it thinks it has discharged less than it has. A real BMS is wrong in exactly these ways.

## Your turn

1. Temporarily change the default `true_capacity_factor` in `battery.py` to 1.0 and rerun `dev_firmware.py`. How does the estimate error change over three days?
2. Plot `soc_fw` and `soc_true` for 14 days. Install matplotlib (`uv add --dev matplotlib`) or print numbers. Does the error grow without bound? What stops it?
3. Print `sim.live_line()` after 12 hours. Paste it into `decode_live` from Part 3. Does it round-trip?

<details>
<summary>Hints</summary>

1. The swing during the evening shrinks to a fraction of a point, because the firmware's idea of the capacity is now right. What remains is the imbalance at the top of charge and the 0.1 % stepping. Put the default back to 0.95 afterwards.
2. It does not grow without bound: every sunny day the charger fills the fullest cell, the true average rises to about 98 %, and the firmware's clamp at 100 % limits how high its estimate can go.
3. It does. `test_the_live_line_always_has_a_valid_crc_and_agrees_with_the_snapshot` checks the same.

</details>

## Checkpoint

- `uv run pytest -q` is green: `49 passed`.
- `dev_firmware.py` prints a believable three days with no errors.
- You can explain the closed loop in two sentences, without looking.

## Commit

```bash
git add services/bms-simulator
git commit -m "feat(bms-simulator): simulator, schema and ThingSet-to-MQTT mapping"
```

Next: [Part 7: The MQTT publisher](07-publisher.md).
