# Part 5: The firmware layer

**Goal.** Write `firmware.py`, a Python re-telling of what the Libre Solar firmware (and the BQ76952 chip) does with raw measurements: protections, the State-of-Charge
estimate, balancing and the state machine. At the end your code prints **exactly the line that the real firmware printed in `native_sim`**.

**Time.** 3 to 4 hours.

## Ideas

**The firmware only knows what the chip tells it.** It gets a `ChipReading`. It never sees the true charge. Everything it "knows" about the State of Charge it has to estimate.

**1. SoC at boot: look it up.** When the BMS starts it reads the cell voltages once and converts the average to a charge with the OCV table:
`soc = interp(avg_cell_v, OCV_V, SOC_PCT)`. Since LFP's voltage curve is flat in the middle, a tiny voltage error means a big charge error. (Real firmware: `bms_soc_reset` in `bms_soc.c`.)

**2. SoC afterwards: count the current.** Every loop the firmware adds `current * elapsed time` to a counter. Only when the counter represents more than 0.1 % of the nominal
capacity does it move the estimate and reset the counter (`bms_soc_update`). The units: with the current in amps and the time in milliseconds, the counter is in
mA·s, and `delta_percent = counter_mAs / (capacity_Ah * 3.6e4)`. Prove it to yourself: 1 Ah is 3600 A·s is 3 600 000 mA·s, and one percent of that is 36 000, which is `3.6e4`.
The estimate is clamped to 0..100. Nothing in this firmware version ever resets it to 100 % when the pack is full (the `full` and `empty` flags exist but nothing sets them), so
errors accumulate until the pack is charged to the top and the clamp hides them.

**3. Protections.** The chip raises **error flags** when a limit is crossed. They are bits in an integer (`rErrorFlags`): bit 0 is under-voltage, bit 1 over-voltage, and so on (the table is in firmware-findings.md and in the code
you will write). A flag is **latched with hysteresis**: it is raised when a value crosses the limit and cleared only when it has come back past a *reset* level. Over-voltage trips above 3.80 V
and clears below 3.40 V. Without hysteresis a voltage hovering at the limit would make the flag flicker.

**4. Allowed or not.** Some flags forbid charging (`CHG_ERRORS`), some forbid discharging (`DIS_ERRORS`). Over-temperature forbids both. Under-voltage forbids only discharging, because charging is how you recover.

**5. The state machine.** `OFF`, `CHG` (only charging allowed), `DIS` (only discharging), `NORMAL` (both), `SHUTDOWN`. The state decides the MOSFET switches. The
firmware runs this machine every loop; a transition takes one loop, so going from `OFF` to `NORMAL` takes two.

**6. Balancing.** If the cells differ, the fuller ones can be bled by a resistor. The chip only does so when the pack has been idle (|current| below 0.1 A) for 1800 s, for cells above 3.30 V that are
more than 10 mV above the lowest cell. Balancing during charging would confuse the voltage measurements.

**7. Quirks of the real output.** With no cells connected, the average voltage is NaN (printed `null`), the minimum reads 10.000 and the maximum 0.000. Unused channels read 0. Our `meas()` copies all that.

## Steps

### 5.1 Tests first

`tests/test_firmware.py`:

```python
import pytest

from chip import MAX_CELLS, ChipReading
from firmware import ERR, NORMAL, OFF, Firmware


def reading(cell_v=3.3, n=8, current=0.0, temp=25.0):
    return ChipReading([cell_v] * n + [0.0] * (MAX_CELLS - n), current, [temp, temp], temp, temp)


def booted(**kw) -> Firmware:
    fw = Firmware()
    fw.boot(reading(**kw))
    return fw


def run(fw, r, seconds, start_ms=0.0, step_s=10):
    t = start_ms
    for _ in range(int(seconds / step_s)):
        t += step_s * 1000
        fw.update(r, t)
    return t


def test_boot_estimates_soc_from_the_average_cell_voltage():
    assert booted(cell_v=3.264).soc == pytest.approx(45.0)     # 3.264 V is the 45 % point


def test_state_machine_goes_off_dis_normal():
    fw = booted()
    assert fw.state == OFF
    fw.update(reading(), 1000)
    assert fw.state != OFF
    fw.update(reading(), 2000)
    assert fw.state == NORMAL


def test_coulomb_counting_moves_soc_by_whole_steps():
    fw = booted(cell_v=3.264)                    # 45 %
    start = fw.soc
    t = run(fw, reading(current=25.0), 3600)     # 25 A for an hour into 50 Ah = +50 %
    assert fw.soc == pytest.approx(min(100.0, start + 50.0), abs=0.2)
    t = run(fw, reading(current=-10.0), 1800, start_ms=t)    # 5 Ah out = -10 %
    assert fw.soc < 100.0


def test_soc_is_clamped():
    fw = booted(cell_v=3.390)
    run(fw, reading(current=50.0), 7200)
    assert fw.soc == 100.0


def test_overvoltage_latches_until_below_the_reset_voltage():
    fw = booted()
    fw.update(reading(cell_v=3.81), 1000)
    assert fw.flags & ERR["CELL_OVERVOLTAGE"]
    fw.update(reading(cell_v=3.60), 2000)     # below the limit, but above the 3.40 reset
    assert fw.flags & ERR["CELL_OVERVOLTAGE"]
    fw.update(reading(cell_v=3.39), 3000)
    assert not fw.flags & ERR["CELL_OVERVOLTAGE"]


def test_undervoltage_blocks_discharge_but_not_charge():
    fw = booted()
    fw.update(reading(cell_v=2.49), 1000)
    assert fw.flags & ERR["CELL_UNDERVOLTAGE"]
    assert not fw.dis_allowed() and fw.chg_allowed()
    fw.update(reading(cell_v=2.49), 2000)
    assert fw.chg_on and not fw.dis_on


def test_over_temperature_blocks_both_and_clears_with_hysteresis():
    fw = booted()
    fw.update(reading(temp=46.0), 1000)
    assert fw.flags & ERR["CHG_OVERTEMP"] and fw.flags & ERR["DIS_OVERTEMP"]
    fw.update(reading(temp=42.0), 2000)         # inside the limit but not 5 degC inside
    assert fw.flags & ERR["CHG_OVERTEMP"]
    fw.update(reading(temp=39.9), 3000)
    assert fw.flags == 0


def test_cold_battery_blocks_charging_only():
    fw = booted()
    fw.update(reading(temp=-5.0), 1000)
    assert fw.flags & ERR["CHG_UNDERTEMP"]
    assert fw.dis_allowed() and not fw.chg_allowed()


def test_balancing_waits_for_30_idle_minutes():
    cells = [3.35, 3.40, 3.35, 3.35, 3.35, 3.35, 3.35, 3.35] + [0.0] * 8
    r = ChipReading(cells, 0.0, [25.0, 25.0], 25.0, 25.0)
    fw = Firmware()
    fw.boot(r)
    t = run(fw, r, 1700)
    assert fw.balancing_mask == 0                # not yet: 1700 s < 1800 s
    run(fw, r, 200, start_ms=t)
    assert fw.balancing_mask == 0b10             # only cell 1 (index 1) is high enough above the rest


def test_load_resets_the_balancing_timer():
    cells = [3.35, 3.40] + [3.35] * 6 + [0.0] * 8
    idle = ChipReading(cells, 0.0, [25.0, 25.0], 25.0, 25.0)
    busy = ChipReading(cells, 5.0, [25.0, 25.0], 25.0, 25.0)
    fw = Firmware()
    fw.boot(idle)
    t = run(fw, idle, 1700)
    t = run(fw, busy, 10, start_ms=t)
    t = run(fw, idle, 1700, start_ms=t)
    assert fw.balancing_mask == 0


def test_meas_has_the_quirks_of_the_real_firmware():
    meas = Firmware().meas()
    assert meas["rCellMinVoltage_V"] == 10.0 and meas["rCellMaxVoltage_V"] == 0.0
    assert len(meas["rCellVoltages_V"]) == MAX_CELLS
```

and the golden test, `tests/test_golden.py`:

```python
"""The most important test of the project: we print exactly what the real firmware prints."""
from chip import ChipReading
from firmware import Firmware
from test_thingset import NATIVE_SIM_LINE
from thingset import encode_live


def test_our_firmware_prints_the_same_line_as_native_sim():
    fw = Firmware()
    fw.boot(ChipReading.empty())
    for k in range(3):                       # OFF -> DIS -> NORMAL needs two passes
        fw.update(ChipReading.empty(), now_ms=1000.0 * k)
    assert encode_live(fw.meas()) == NATIVE_SIM_LINE
```

Read the test names top to bottom. Together they are the spec of this part. The golden test imports `NATIVE_SIM_LINE` from `test_thingset.py`, the line
you captured. If it passes, then your whole chain (chip reading, firmware, line encoder) reproduces the real firmware for the empty chip, **including the CRC**.

### 5.2 Skeleton

Create `firmware.py` starting from this. Everything that is *data* is given. Everything that is *behaviour* you write.

```python
from __future__ import annotations

import math
from dataclasses import dataclass, field

from chemistry import OCV_V, SOC_PCT, interp
from chip import MAX_CELLS, NOT_CONNECTED_V, ChipReading

# Error flag bit positions (BMS_ERR_* in the firmware), with the names of docs/mqtt-schema.md
ERROR_BITS = [
    "CELL_UNDERVOLTAGE", "CELL_OVERVOLTAGE", "SHORT_CIRCUIT", "DIS_OVERCURRENT",
    "CHG_OVERCURRENT", "OPEN_WIRE", "DIS_UNDERTEMP", "DIS_OVERTEMP", "CHG_UNDERTEMP",
    "CHG_OVERTEMP", "INT_OVERTEMP", "CELL_FAILURE", "DIS_OFF", "CHG_OFF", "FET_OVERTEMP",
]
ERR = {name: 1 << i for i, name in enumerate(ERROR_BITS)}      # name -> bit mask

# bms_chg_error() / bms_dis_error(): which flags forbid charging / discharging
CHG_ERRORS = (ERR["CELL_OVERVOLTAGE"] | ERR["CHG_OVERCURRENT"] | ERR["OPEN_WIRE"]
              | ERR["CHG_UNDERTEMP"] | ERR["CHG_OVERTEMP"] | ERR["INT_OVERTEMP"]
              | ERR["CELL_FAILURE"] | ERR["CHG_OFF"])
DIS_ERRORS = (ERR["CELL_UNDERVOLTAGE"] | ERR["SHORT_CIRCUIT"] | ERR["DIS_OVERCURRENT"]
              | ERR["OPEN_WIRE"] | ERR["DIS_UNDERTEMP"] | ERR["DIS_OVERTEMP"]
              | ERR["INT_OVERTEMP"] | ERR["CELL_FAILURE"] | ERR["DIS_OFF"])

OFF, CHG, DIS, NORMAL, SHUTDOWN = range(5)                     # rBmsState
STATE_NAMES = ["OFF", "CHG", "DIS", "NORMAL", "SHUTDOWN"]


@dataclass
class Config:
    """Defaults of bms_init_config() for an LFP battery (`?Conf` in the ThingSet shell)."""
    capacity_ah: float = 50.0
    cell_ov: float = 3.80
    cell_ov_reset: float = 3.40
    cell_uv: float = 2.50
    cell_uv_reset: float = 3.10
    chg_ot: float = 45.0
    dis_ot: float = 45.0
    chg_ut: float = 0.0
    dis_ut: float = -20.0
    temp_hyst: float = 5.0
    bal_idle_delay_s: float = 1800.0
    bal_idle_current_a: float = 0.1
    bal_diff_v: float = 0.010
    bal_min_v: float = 3.30
    # Not a firmware setting: the real chip has its own permanent-failure checks; we approximate.
    cell_failure_diff_v: float = 0.40

    # chg_oc, dis_oc (both = capacity, i.e. 1C) and short_circuit (2 x dis_oc) as @property
    ...


@dataclass
class Firmware:
    cfg: Config = field(default_factory=Config)

    def __post_init__(self) -> None:
        ...   # flags=0, state=OFF, soc=0.0, the coulomb counter, timestamps, balancing_mask=0, reading=ChipReading.empty()

    @property
    def cells(self) -> list[float]: ...          # voltages of connected cells only
    def chg_allowed(self) -> bool: ...
    def dis_allowed(self) -> bool: ...
    def boot(self, reading: ChipReading) -> None: ...
    def update(self, reading: ChipReading, now_ms: float) -> None: ...
    def _latch(self, name: str, trip: bool, clear: bool) -> None: ...
    def _protections(self) -> None: ...
    def _update_soc(self, now_ms: float) -> None: ...
    def _balancing(self, now_ms: float) -> None: ...
    def _state_machine(self) -> None: ...
    @property
    def chg_on(self) -> bool: ...
    @property
    def dis_on(self) -> bool: ...
    def meas(self) -> dict: ...
```

### 5.3 Build it in this order, running tests as you go

**A. Properties and allowed.** `cells` = the entries of `self.reading.cell_v` greater than `NOT_CONNECTED_V`. `chg_allowed` is true when **no bit of `CHG_ERRORS` is set in `self.flags`** (use `&`).
The real firmware ignores the `CHG_OFF` flag in this test, because that flag only reports that the switch *is* off. So mask it out: `self.flags & CHG_ERRORS & ~ERR["CHG_OFF"]`. Same for discharge with `DIS_OFF`.

Tests: `-k "state_machine"` will need more, so go on first.

**B. `boot`.** Store the reading. Compute the average of the connected cells (0.0 if none) and set `self.soc = interp(avg, OCV_V, SOC_PCT)`. Reset the coulomb counter and the "last update" time to `None`.
Test: `-k boot`.

**C. `_latch`, then `_protections`.** `_latch(name, trip, clear)`: if `trip` set the flag bit (`|=`); elif `clear` clear it (`&= ~mask`); otherwise leave it as it was. That one function gives you hysteresis everywhere.
In `_protections` call it for each flag with the right *trip* and *clear* conditions:

| flag | trips when | clears when |
| --- | --- | --- |
| `CELL_OVERVOLTAGE` | highest cell `> cell_ov` | highest cell `< cell_ov_reset` |
| `CELL_UNDERVOLTAGE` | lowest cell `< cell_uv` | lowest cell `> cell_uv_reset` |
| `CELL_FAILURE` | `max - min > cell_failure_diff_v` | `max - min < 0.7 * cell_failure_diff_v` |
| `CHG_OVERTEMP` / `DIS_OVERTEMP` | hottest cell temp `> chg_ot` / `dis_ot` | `< limit - temp_hyst` |
| `CHG_UNDERTEMP` / `DIS_UNDERTEMP` | coldest temp `< chg_ut` / `dis_ut` | `> limit + temp_hyst` |
| `CHG_OVERCURRENT` | current `> chg_oc` | current `< 0.9 * chg_oc` |
| `DIS_OVERCURRENT` | `-current > dis_oc` | `-current < 0.9 * dis_oc` |
| `SHORT_CIRCUIT` | `-current > short_circuit` | `-current < 0.5 * short_circuit` |

Cell checks only make sense if there are connected cells (with `native_sim`'s empty chip there are none, and no flag may trip). Ignore temperatures at or below -200, which means "no sensor".
The real chip also waits 2 s (voltages) or 320 ms (current) before tripping. We do not model those delays, because our time steps are much bigger than that.

Tests: `-k "overvoltage or undervoltage or temperature or cold"`.

**D. `_update_soc`.** Exactly the algorithm in "Ideas, 2". First call (`_last_ms is None`): remember `now_ms` and return. Otherwise add `current_a * (now_ms - _last_ms)` to the counter, update `_last_ms`,
compute `delta = counter / (cfg.capacity_ah * 3.6e4)`, and if `abs(delta) > 0.1` apply it (clamped to 0..100) and zero the counter.
Tests: `-k "coulomb or clamped"`.

**E. `_balancing`.** Keep `_idle_since_ms`. If `abs(current) >= bal_idle_current_a` set it to `None`; elif it is `None` set it to `now_ms`. The idle time in seconds is `0` when `_idle_since_ms` is `None`, else `(now_ms - _idle_since_ms) / 1000`.
If the idle time is at least `bal_idle_delay_s` and there are cells: for each channel `k` with a connected cell whose voltage is above `bal_min_v` and more than `bal_diff_v` above the lowest cell, set bit `k` in `balancing_mask`.
Tests: `-k balancing`.

**F. `_state_machine` and `update`.** Compute `chg = chg_allowed()` and `dis = dis_allowed()`, then follow this table (exactly one transition per call):

| from | rule |
| --- | --- |
| `OFF` | if `dis`: go `DIS`; elif `chg`: go `CHG` |
| `CHG` | if not `chg`: `OFF`; elif `dis`: `NORMAL` |
| `DIS` | if not `dis`: `OFF`; elif `chg`: `NORMAL` |
| `NORMAL` | if not `dis`: `CHG`; elif not `chg`: `DIS` |

`chg_on` is true in `CHG` and `NORMAL`; `dis_on` in `DIS` and `NORMAL`. `update(reading, now_ms)` stores the reading and calls, in this order, `_protections`, `_update_soc`, `_balancing`, `_state_machine`.
Tests: `-k "state_machine or blocks or undervoltage"`.

**G. `meas()`.** Returns the 14 items of `Meas` as a dict (names as in `thingset.ITEMS`):

- `rPackVoltage_V` and `rStackVoltage_V`: the sum of the connected cells.
- `rPackCurrent_A`, `rCellTemps_degC` (a list), `rICTemp_degC`, `rMOSFETTemp_degC`: straight from the reading.
- `rSOC_pct`, `rErrorFlags`, `rBmsState`: the firmware's own values.
- `rCellVoltages_V`: the full 16-entry list.
- `rCellAvgVoltage_V`: the average of the connected cells, or `math.nan` if there are none. `rCellMinVoltage_V`: the minimum, or `10.0` if none. `rCellMaxVoltage_V`: the maximum, or `0.0` if none.
- `rBalancingStatus`: the mask.

### 5.4 Run everything

```bash
uv run pytest tests/test_firmware.py tests/test_golden.py -q
```

Expected: `12 passed` (11 firmware tests and the golden test). If the golden test fails, pytest shows both lines in full. Compare them. A typical difference is a state of 0 or 2 instead of 3 (your state machine is
missing a transition), or `rCellAvgVoltage_V` of `0.000` instead of `null` (your `meas()` did not return NaN).

<details>
<summary>Solution: firmware.py</summary>

```python
"""What the Libre Solar BMS firmware does with the raw chip data.

This file is a Python re-telling of three C files of the real firmware:
  bms_common.c  default configuration, error -> allowed/not allowed, state machine
  bms_soc.c     State-of-Charge estimate
  the BQ76952 chip protections and auto-balancing (done by the chip, not by the firmware)

It sees ONLY what the chip can measure (a ChipReading).  It never sees the true charge.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from chemistry import OCV_V, SOC_PCT, interp
from chip import MAX_CELLS, NOT_CONNECTED_V, ChipReading

# Error flag bit positions (BMS_ERR_* in the firmware), and the names of docs/mqtt-schema.md
ERROR_BITS = [
    "CELL_UNDERVOLTAGE", "CELL_OVERVOLTAGE", "SHORT_CIRCUIT", "DIS_OVERCURRENT",
    "CHG_OVERCURRENT", "OPEN_WIRE", "DIS_UNDERTEMP", "DIS_OVERTEMP", "CHG_UNDERTEMP",
    "CHG_OVERTEMP", "INT_OVERTEMP", "CELL_FAILURE", "DIS_OFF", "CHG_OFF", "FET_OVERTEMP",
]
ERR = {name: 1 << i for i, name in enumerate(ERROR_BITS)}

# bms_chg_error() / bms_dis_error(): which flags forbid charging / discharging
CHG_ERRORS = (ERR["CELL_OVERVOLTAGE"] | ERR["CHG_OVERCURRENT"] | ERR["OPEN_WIRE"]
              | ERR["CHG_UNDERTEMP"] | ERR["CHG_OVERTEMP"] | ERR["INT_OVERTEMP"]
              | ERR["CELL_FAILURE"] | ERR["CHG_OFF"])
DIS_ERRORS = (ERR["CELL_UNDERVOLTAGE"] | ERR["SHORT_CIRCUIT"] | ERR["DIS_OVERCURRENT"]
              | ERR["OPEN_WIRE"] | ERR["DIS_UNDERTEMP"] | ERR["DIS_OVERTEMP"]
              | ERR["INT_OVERTEMP"] | ERR["CELL_FAILURE"] | ERR["DIS_OFF"])

OFF, CHG, DIS, NORMAL, SHUTDOWN = range(5)      # rBmsState
STATE_NAMES = ["OFF", "CHG", "DIS", "NORMAL", "SHUTDOWN"]

@dataclass
class Config:
    """Defaults of bms_init_config() for an LFP battery (`?Conf` in the ThingSet shell)."""
    capacity_ah: float = 50.0
    cell_ov: float = 3.80
    cell_ov_reset: float = 3.40
    cell_uv: float = 2.50
    cell_uv_reset: float = 3.10
    chg_ot: float = 45.0
    dis_ot: float = 45.0
    chg_ut: float = 0.0
    dis_ut: float = -20.0
    temp_hyst: float = 5.0
    bal_idle_delay_s: float = 1800.0
    bal_idle_current_a: float = 0.1
    bal_diff_v: float = 0.010
    bal_min_v: float = 3.30
    # Not a firmware setting: the real chip has its own permanent-failure checks; we approximate.
    cell_failure_diff_v: float = 0.40

    @property
    def chg_oc(self) -> float:      # 1C
        return self.capacity_ah

    @property
    def dis_oc(self) -> float:
        return self.capacity_ah

    @property
    def short_circuit(self) -> float:   # 2 x the discharge limit
        return 2 * self.dis_oc


@dataclass
class Firmware:
    cfg: Config = field(default_factory=Config)

    def __post_init__(self) -> None:
        self.flags = 0
        self.state = OFF
        self.soc = 0.0
        self._coulomb_mas = 0.0
        self._last_ms: float | None = None
        self._idle_since_ms: float | None = None
        self.balancing_mask = 0
        self.reading = ChipReading.empty()

    # ------------------------------------------------------------------ helpers
    @property
    def cells(self) -> list[float]:
        """Voltages of the connected cells only."""
        return [v for v in self.reading.cell_v if v > NOT_CONNECTED_V]

    def chg_allowed(self) -> bool:
        return not (self.flags & CHG_ERRORS & ~ERR["CHG_OFF"])

    def dis_allowed(self) -> bool:
        return not (self.flags & DIS_ERRORS & ~ERR["DIS_OFF"])

    # ------------------------------------------------------------------ boot
    def boot(self, reading: ChipReading) -> None:
        """main(): read the cell voltages once, then bms_soc_reset(-1)."""
        self.reading = reading
        cells = self.cells
        avg = sum(cells) / len(cells) if cells else 0.0
        self.soc = interp(avg, OCV_V, SOC_PCT)
        self._coulomb_mas = 0.0
        self._last_ms = None

    # ------------------------------------------------------------------ one loop iteration
    def update(self, reading: ChipReading, now_ms: float) -> None:
        """One pass of the main loop: read chip, update SoC, run the state machine."""
        self.reading = reading
        self._protections()
        self._update_soc(now_ms)
        self._balancing(now_ms)
        self._state_machine()

    def _latch(self, name: str, trip: bool, clear: bool) -> None:
        """Set a flag when `trip`; clear it only once `clear` (hysteresis)."""
        if trip:
            self.flags |= ERR[name]
        elif clear:
            self.flags &= ~ERR[name]

    def _protections(self) -> None:
        """The chip's protection functions.  Delays (2 s, 320 ms) are not modelled."""
        c, r = self.cfg, self.reading
        cells = self.cells
        if cells:
            hi, lo = max(cells), min(cells)
            self._latch("CELL_OVERVOLTAGE", hi > c.cell_ov, hi < c.cell_ov_reset)
            self._latch("CELL_UNDERVOLTAGE", lo < c.cell_uv, lo > c.cell_uv_reset)
            self._latch("CELL_FAILURE", hi - lo > c.cell_failure_diff_v,
                        hi - lo < 0.7 * c.cell_failure_diff_v)
            temps = [t for t in r.cell_temps_c if t > -200]     # ignore "no sensor"
            if temps:
                t_hi, t_lo = max(temps), min(temps)
                self._latch("CHG_OVERTEMP", t_hi > c.chg_ot, t_hi < c.chg_ot - c.temp_hyst)
                self._latch("DIS_OVERTEMP", t_hi > c.dis_ot, t_hi < c.dis_ot - c.temp_hyst)
                self._latch("CHG_UNDERTEMP", t_lo < c.chg_ut, t_lo > c.chg_ut + c.temp_hyst)
                self._latch("DIS_UNDERTEMP", t_lo < c.dis_ut, t_lo > c.dis_ut + c.temp_hyst)
        i = r.current_a
        self._latch("CHG_OVERCURRENT", i > c.chg_oc, i < 0.9 * c.chg_oc)
        self._latch("DIS_OVERCURRENT", -i > c.dis_oc, -i < 0.9 * c.dis_oc)
        self._latch("SHORT_CIRCUIT", -i > c.short_circuit, -i < 0.5 * c.short_circuit)

    def _update_soc(self, now_ms: float) -> None:
        """bms_soc_update(): coulomb counting, applied in steps bigger than 0.1 %."""
        if self._last_ms is None:
            self._last_ms = now_ms
            return
        self._coulomb_mas += self.reading.current_a * (now_ms - self._last_ms)   # A * ms = mA * s
        self._last_ms = now_ms
        delta = self._coulomb_mas / (self.cfg.capacity_ah * 3.6e4)               # percent
        if abs(delta) > 0.1:
            self.soc = min(100.0, max(0.0, self.soc + delta))
            self._coulomb_mas = 0.0

    def _balancing(self, now_ms: float) -> None:
        """Chip auto-balancing: only after the pack was idle for a long time."""
        c = self.cfg
        if abs(self.reading.current_a) >= c.bal_idle_current_a:
            self._idle_since_ms = None
        elif self._idle_since_ms is None:
            self._idle_since_ms = now_ms
        idle_s = 0.0 if self._idle_since_ms is None else (now_ms - self._idle_since_ms) / 1000.0
        mask = 0
        cells = self.cells
        if idle_s >= c.bal_idle_delay_s and cells:
            lowest = min(cells)
            for k, v in enumerate(self.reading.cell_v):
                if v > NOT_CONNECTED_V and v > c.bal_min_v and v - lowest > c.bal_diff_v:
                    mask |= 1 << k
        self.balancing_mask = mask

    def _state_machine(self) -> None:
        """bms_state_machine(), without the ideal-diode branches (the BQ76952 does those)."""
        chg, dis = self.chg_allowed(), self.dis_allowed()
        if self.state == OFF:
            if dis:
                self.state = DIS
            elif chg:
                self.state = CHG
        elif self.state == CHG:
            if not chg:
                self.state = OFF
            elif dis:
                self.state = NORMAL
        elif self.state == DIS:
            if not dis:
                self.state = OFF
            elif chg:
                self.state = NORMAL
        elif self.state == NORMAL:
            if not dis:
                self.state = CHG
            elif not chg:
                self.state = DIS

    @property
    def chg_on(self) -> bool:
        return self.state in (CHG, NORMAL)

    @property
    def dis_on(self) -> bool:
        return self.state in (DIS, NORMAL)

    # ------------------------------------------------------------------ output
    def meas(self) -> dict:
        """The `Meas` group, with the same items (and the same quirks) as the real firmware."""
        r = self.reading
        connected = self.cells
        pack_v = sum(connected)
        return {
            "rPackVoltage_V": pack_v,
            "rStackVoltage_V": pack_v,
            "rPackCurrent_A": r.current_a,
            "rCellTemps_degC": list(r.cell_temps_c),
            "rICTemp_degC": r.ic_temp_c,
            "rMOSFETTemp_degC": r.mosfet_temp_c,
            "rSOC_pct": self.soc,
            "rErrorFlags": self.flags,
            "rBmsState": self.state,
            "rCellVoltages_V": list(r.cell_v),
            "rCellAvgVoltage_V": (pack_v / len(connected)) if connected else math.nan,
            "rCellMinVoltage_V": min(connected) if connected else 10.0,
            "rCellMaxVoltage_V": max(connected) if connected else 0.0,
            "rBalancingStatus": self.balancing_mask,
        }
```

Three things to notice. (1) `_latch` is the only place where a flag changes, so hysteresis is guaranteed everywhere. (2) `update()` has no access to the battery's true charge. It is not a parameter anywhere.
(3) `meas()` returns floats and `math.nan`, and leaves *printing* to `thingset.py`. Computing and formatting are different jobs.

</details>

### 5.5 See the golden line yourself

Create `dev_thingset.py`:

```python
"""Part 3: print the line our code produces for the empty chip of native_sim."""
from firmware import ChipReading, Firmware
from thingset import decode_live, encode_live

fw = Firmware()
fw.boot(ChipReading.empty())
for k in range(3):
    fw.update(ChipReading.empty(), now_ms=1000.0 * k)

line = encode_live(fw.meas())
print(line)
print("CRC ok, BMS state =", decode_live(line)["rBmsState"])
```

```bash
uv run python dev_thingset.py
```

```text
#mLive {"Meas":{"rPackVoltage_V":0.00,"rStackVoltage_V":0.00,"rPackCurrent_A":0.00,"rCellTemps_degC":[-273.1,0.0],"rICTemp_degC":-273.1,"rMOSFETTemp_degC":-273.1,"rSOC_pct":0.0,"rErrorFlags":0,"rBmsState":3,"rCellVoltages_V":[0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000],"rCellAvgVoltage_V":null,"rCellMinVoltage_V":10.000,"rCellMaxVoltage_V":0.000,"rBalancingStatus":0}} 437123CA#
CRC ok, BMS state = 3
```

Now compare with the line from your own `native_sim` terminal, character by character. It is the same, down to the CRC `437123CA`. That is the moment this simulator stops being "a model that looks like a BMS" and becomes
"a program that speaks like the real firmware."

## Your turn

1. In `test_firmware.py` find the test for balancing and change 1700 to 1900. Does it still pass? Explain what the test is checking.
2. Add a test that a pack at 3.45 V per cell and idle for an hour with **all cells equal** does *not* balance. Which condition makes that true?
3. The firmware trips `CELL_OVERVOLTAGE` at 3.80 V, but the charger tapers long before that. Look at `battery.py`: can any normal scenario reach 3.80 V? Write down what you would change in the battery to make an `overcharge` scenario.

<details>
<summary>Hints</summary>

1. It then fails, because after 1900 s idle the balancing *has* started, and the test asserts that after 1700 s it has not. The test pins down the 30-minute rule from both sides.
2. The rule is "more than `bal_diff_v` above the lowest cell". With equal cells nobody is above the lowest.
3. No: the taper holds the cells at or below 3.392 V (100 %). An overcharge scenario needs a charger that ignores the taper, so the true charge exceeds 1.0 and the OCV table needs an extension above 100 %, like the one you added below 0 %.

</details>

## Checkpoint

- `uv run pytest -q` passes everything so far: 4 + 5 + 13 + 11 + 1 plus your health test, so `35 passed`.
- `dev_thingset.py` prints the same line as `native_sim`.
- You can answer: "Why can the firmware report a different State of Charge from the cells' real one?" (Because it is an estimate: it started from a flat part of the OCV curve, it counts with a nominal capacity that is 5 % too big, and it only applies the counter in steps of 0.1 %.)

## Commit

```bash
git add services/bms-simulator
git commit -m "feat(bms-simulator): firmware layer, golden test against native_sim"
```

Next: [Part 6: Glue and mapping](06-glue-and-mapping.md).
