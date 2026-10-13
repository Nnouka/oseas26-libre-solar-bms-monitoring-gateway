# Part 4: The physical battery

**Goal.** Model a pack of eight LiFePO4 cells as *physics*: charge, voltage, current, heat. No protections, no State-of-Charge estimate, no MQTT. When you finish
you will have `chemistry.py`, `chip.py` and `physical_battery.py`, and a script that prints a day in the life of the battery.

**Time.** 3 to 4 hours. This is the longest part, and the most fun.

## Ideas (read these first, then come back to them while coding)

**1. State of charge (truth).** A cell holds a charge `soc` between 0 (empty) and 1 (full). Current into the cell changes it:

```text
soc_new = soc + I * dt / (capacity_Ah * 3600)          I in A, dt in s, capacity in Ah
```

One amp for one hour is one amp-hour, and `capacity_Ah * 3600` is the capacity in coulombs (ampere-seconds). This is called **coulomb counting**.

**2. Open-circuit voltage (OCV).** A resting cell has a voltage that depends on `soc`. For LFP the curve is almost flat in the middle (3.26 to 3.31 V
between 20 % and 90 %) and steep at the ends. This flatness is why SoC is hard to estimate from voltage for LFP. You will see the effect in Part 5.
The curve is a table of 21 points. Between points we interpolate linearly.

**3. Internal resistance.** When current flows, the terminal voltage differs from the OCV by `I * R`:

```text
v_terminal = OCV(soc) + I * R        (I positive when charging: the voltage rises)
```

Each cell has a slightly different `R` (about 1 mΩ).

**4. Cells are never identical.** Slightly different capacity, resistance and initial charge. These small differences are what the balancing logic in Part 5
exists for. We also make the cells **5 % smaller than the label says** (a worn battery), which the firmware does not know. That is how its estimate gets
wrong later.

**5. Heat.** Current through resistance produces heat `P = I² * R_total`. The pack is a lump with a heat capacity and cools towards the ambient temperature:

```text
dT = (P_heat - k * (T - T_ambient)) * dt / C_thermal
```

**6. The current profile.** A solar charger and a load decide the battery current, not the battery. A sine of sunlight from 06:00 to 18:00 charges, a
constant night load and an evening peak discharge. When the pack is full the charger *tapers* and the battery current falls to zero. The sun is not always out: a `cloudy`
scenario lets the panel deliver only a quarter of its clear-sky current, so you can see how the pack copes with a gloomy spell.

**7. The switches.** The BMS has two logical switches, charge and discharge. On the real C1 board each is built from MOSFETs in a back-to-back pair, with 4 such pairs in parallel to carry the current. The simulator models them as just two on/off inputs. If the charge switch is open no charging current can flow, and likewise for discharge. The battery model takes the switch states
as inputs. They come from the firmware in Part 5. This is the loop that closes in Part 6.

**8. Time steps.** A simulation advances in steps of `dt`. Too large and it becomes inaccurate, so the next part limits one step to 30 simulated seconds.

## Steps

### 4.1 `chemistry.py`: the OCV table and interpolation

Tests first: `tests/test_chemistry.py`.

```python
import pytest

from chemistry import OCV_V, SOC_PCT, interp


def test_tables_have_matching_lengths_and_increase():
    assert len(OCV_V) == len(SOC_PCT) == 21
    assert SOC_PCT == sorted(SOC_PCT)
    assert OCV_V == sorted(OCV_V)


def test_interp_hits_the_table_points():
    assert interp(0.0, SOC_PCT, OCV_V) == 2.833
    assert interp(45.0, SOC_PCT, OCV_V) == pytest.approx(3.264)
    assert interp(100.0, SOC_PCT, OCV_V) == 3.392


def test_interp_is_halfway_between_two_points():
    assert interp(92.5, SOC_PCT, OCV_V) == pytest.approx((3.309 + 3.314) / 2)


def test_interp_clamps_outside_the_table():
    assert interp(-10.0, SOC_PCT, OCV_V) == 2.833
    assert interp(250.0, SOC_PCT, OCV_V) == 3.392


def test_interp_works_the_other_way_round_too():
    # voltage -> charge: this is how the firmware estimates SoC at boot
    assert interp(3.264, OCV_V, SOC_PCT) == pytest.approx(45.0)
    assert interp(0.0, OCV_V, SOC_PCT) == 0.0
    assert interp(9.0, OCV_V, SOC_PCT) == 100.0
```

Create `chemistry.py`. It holds two lists of 21 numbers and one function. The voltages are the firmware's own table (you saw it in `?Conf`
as `sOcvPoints_V`), reversed here so that charge *increases*:

```python
SOC_PCT = [5.0 * i for i in range(21)]      # 0, 5, 10 ... 100
OCV_V = [2.833, 3.132, 3.177, 3.190, 3.213, 3.226, 3.240, 3.252, 3.262, 3.264, 3.265,
         3.268, 3.271, 3.275, 3.283, 3.296, 3.304, 3.308, 3.309, 3.314, 3.392]


def interp(x: float, xs: list[float], ys: list[float]) -> float:
    """Linear interpolation of y at x, given sample points (xs, ys) with xs increasing.

    Outside the table the first / last y value is returned (no extrapolation).
    """
    ...
```

**Algorithm for `interp`** (try it on paper with `x = 92.5`):

1. If `x` is at or below `xs[0]`, return `ys[0]`. If at or above `xs[-1]`, return `ys[-1]`.
2. Find the first index `i` with `x <= xs[i]`. The point lies between `xs[i-1]` and `xs[i]`.
3. `fraction = (x - xs[i-1]) / (xs[i] - xs[i-1])`, which is 0 at the left end and 1 at the right end.
4. Return `ys[i-1] + fraction * (ys[i] - ys[i-1])`.

Run `uv run pytest tests/test_chemistry.py -q`, expecting `5 passed`.

<details>
<summary>Solution: chemistry.py</summary>

```python
"""Cell chemistry: how the open-circuit voltage (OCV) of one LiFePO4 cell depends on its charge.

The numbers are copied from `ocv_lfp` in the Libre Solar firmware (app/src/bms_common.c), where they
are listed from 100 % down to 0 %.  Here they are listed from 0 % up, because that is the natural
order for a lookup table.  Both the physical battery (as the truth) and the firmware (as its
estimate) use them.
"""
from __future__ import annotations

SOC_PCT = [5.0 * i for i in range(21)]                      # 0, 5, 10 ... 100
OCV_V = [2.833, 3.132, 3.177, 3.190, 3.213, 3.226, 3.240, 3.252, 3.262, 3.264, 3.265,
         3.268, 3.271, 3.275, 3.283, 3.296, 3.304, 3.308, 3.309, 3.314, 3.392]


def interp(x: float, xs: list[float], ys: list[float]) -> float:
    """Linear interpolation of y at x, given sample points (xs, ys) with xs increasing.

    Outside the table the first / last y value is returned (no extrapolation).
    """
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            fraction = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + fraction * (ys[i] - ys[i - 1])
    return ys[-1]          # unreachable; keeps type checkers quiet
```

The same function converts charge to voltage (`interp(soc, SOC_PCT, OCV_V)`) and voltage to charge (`interp(v, OCV_V, SOC_PCT)`), because both tables increase together.
The firmware does the second one at boot.

</details>

### 4.2 `chip.py`: what the chip measures

This tiny file is the boundary between the two halves of the simulator. The battery *produces* a `ChipReading`, the firmware *consumes* it. The firmware
can never see anything else, just like on the real board.

```python
from __future__ import annotations

from dataclasses import dataclass

MAX_CELLS = 16          # the C1 reports 16 cell channels, connected or not
NOT_CONNECTED_V = 0.5   # the real driver treats a channel below 0.5 V as "no cell"


@dataclass
class ChipReading:
    ...                 # five fields: see the solution for names and meaning
```

Think about which fields a BQ76952 delivers: the voltage of each cell channel (a list of 16, with 0.0 for unused channels), the pack
current, the cell temperatures (the C1 has two thermistors), the chip's own temperature, and the MOSFET temperature. Then add a
`classmethod` called `empty()` that returns what the native_sim chip returns (you saw it in Part 0: no cells, temperatures read -273.15 or 0).

<details>
<summary>Solution: chip.py</summary>

```python
"""The interface between the physical battery and the firmware: what the BQ76952 chip measures."""
from __future__ import annotations

from dataclasses import dataclass

MAX_CELLS = 16          # the C1 reports 16 cell channels, connected or not
NOT_CONNECTED_V = 0.5   # the real driver treats a channel below 0.5 V as "no cell"


@dataclass
class ChipReading:
    """Everything the chip hands to the firmware in one poll."""
    cell_v: list[float]            # MAX_CELLS entries, 0.0 where no cell is connected
    current_a: float               # + = charging
    cell_temps_c: list[float]
    ic_temp_c: float
    mosfet_temp_c: float

    @classmethod
    def empty(cls) -> "ChipReading":
        """What native_sim's emulated chip returns: no cells, no sensors."""
        return cls([0.0] * MAX_CELLS, 0.0, [-273.15, 0.0], -273.15, -273.15)
```

A `dataclass` writes `__init__` for you from the field list. `ChipReading.empty()` is the "alternative constructor" pattern.

</details>

### 4.3 `physical_battery.py`: tests first

```python
"""The physical battery on its own: no firmware involved."""
import pytest

from physical_battery import SCENARIOS, TAPER_BAND, Battery, ocv
from chip import MAX_CELLS


def cells_of(reading):
    return [v for v in reading.cell_v if v > 0.5]


def test_ocv_is_flat_in_the_middle_and_collapses_below_zero():
    assert ocv(0.5) == pytest.approx(3.265, abs=0.005)
    assert ocv(1.0) == pytest.approx(3.392)
    assert ocv(-0.05) < 2.5


def test_the_cells_are_not_identical_and_the_chip_pads_to_16_channels():
    b = Battery(cells=8, seed=1)
    reading = b.read_chip()
    assert len(reading.cell_v) == MAX_CELLS
    assert reading.cell_v[8:] == [0.0] * 8
    assert len(set(cells_of(reading))) > 1


def test_same_seed_same_battery():
    a, b = Battery(seed=5), Battery(seed=5)
    assert a.soc == b.soc and a.cap == b.cap


def test_charging_raises_the_true_charge():
    b = Battery(seed=1, start_hour=9.0)
    before = sum(b.soc)
    b.step(3600, chg_on=True, dis_on=True, bleed_mask=0)
    assert sum(b.soc) > before and b.current_a > 0


def test_discharging_lowers_the_true_charge():
    b = Battery(seed=1, start_hour=19.0)
    before = sum(b.soc)
    b.step(3600, chg_on=True, dis_on=True, bleed_mask=0)
    assert sum(b.soc) < before and b.current_a < 0


def test_an_open_switch_stops_the_current():
    b = Battery(seed=1, start_hour=9.0)
    b.step(60, chg_on=False, dis_on=True, bleed_mask=0)
    assert b.current_a == 0.0
    b = Battery(seed=1, start_hour=19.0)
    b.step(60, chg_on=True, dis_on=False, bleed_mask=0)
    assert b.current_a == 0.0


def test_the_charger_tapers_to_zero_when_the_pack_is_full():
    b = Battery(seed=1, start_hour=9.0)
    for _ in range(36):                                # three hours of morning sun
        b.step(300, chg_on=True, dis_on=True, bleed_mask=0)
    assert max(b.soc) <= 1.0 + 1e-9
    assert abs(b.current_a) < 0.5


def test_current_makes_the_terminal_voltage_sag_or_rise():
    b = Battery(seed=1)
    rest = b.terminal_voltages(0.0)
    assert all(v > r for v, r in zip(b.terminal_voltages(20.0), rest))
    assert all(v < r for v, r in zip(b.terminal_voltages(-20.0), rest))


def test_current_heats_the_pack_and_it_cools_back():
    b = Battery(seed=1, start_hour=0.0)
    b.temp_c = 28.0
    b.current_a = 0.0
    b.temp_c += 5.0                                    # heat it by hand
    for _ in range(30):
        b.step(300, chg_on=False, dis_on=False, bleed_mask=0)   # no current, only cooling
    assert b.temp_c < 29.0


def test_bleeding_lowers_one_cell_only():
    a, b = Battery(seed=1, start_hour=3.0), Battery(seed=1, start_hour=3.0)
    a.step(3600, False, False, bleed_mask=0b100)
    b.step(3600, False, False, bleed_mask=0)
    assert a.soc[2] < b.soc[2]
    assert a.soc[1] == pytest.approx(b.soc[1])


def test_scenarios_are_known_and_unknown_ones_rejected():
    assert SCENARIOS == ("normal", "cell_drift", "overheat", "cloudy")
    with pytest.raises(ValueError):
        Battery().set_scenario("bogus")


def test_overheat_raises_the_ambient_temperature():
    b = Battery(seed=1)
    b.set_scenario("overheat")
    for _ in range(60):
        b.step(60, True, True, 0)
    assert b.ambient_c > 40.0


def test_cell_drift_pulls_one_cell_down():
    b = Battery(seed=1)
    b.set_scenario("cell_drift")
    for _ in range(300):
        b.step(60, False, False, 0)
    assert b.soc[2] < min(b.soc[k] for k in range(8) if k != 2) - 0.1


def test_cloudy_midday_current_is_much_lower_than_clear_sky():
    clear, cloudy = Battery(seed=1, start_hour=12.0), Battery(seed=1, start_hour=12.0)
    cloudy.set_scenario("cloudy")
    clear.step(60, True, True, 0)
    cloudy.step(60, True, True, 0)
    assert 0.0 < cloudy.current_a < 0.4 * clear.current_a


def test_a_cloudy_day_does_not_fill_the_pack():
    clear, cloudy = Battery(seed=1, start_hour=6.0), Battery(seed=1, start_hour=6.0)
    cloudy.set_scenario("cloudy")
    clear_peak = cloudy_peak = 0.0
    for _ in range(72):                                # 06:00 to 18:00 in 10-minute steps
        clear.step(600, True, True, 0)
        cloudy.step(600, True, True, 0)
        clear_peak = max(clear_peak, max(clear.soc))
        cloudy_peak = max(cloudy_peak, max(cloudy.soc))
    assert clear_peak > 0.99                           # the sunny pack fills up...
    assert cloudy_peak < 1.0 - TAPER_BAND              # ...the cloudy one never even reaches the taper zone
    assert sum(cloudy.soc) < sum(clear.soc)


def test_leaving_the_cloudy_scenario_brings_the_sun_back():
    b = Battery(seed=1, start_hour=12.0)
    b.set_scenario("cloudy")
    assert b.cloud < 1.0
    b.set_scenario("normal")
    assert b.cloud == 1.0
```

Read the test names. They are a complete specification of the physical model. Notice how they use the *switch* arguments and `bleed_mask`, which you meet next.

### 4.4 `physical_battery.py`: build it in five small steps

You will write one class, `Battery`. Do the steps in order and run the matching tests after each. Run `uv run pytest tests/test_battery.py -q -k <name>` to run a single test.

**Step A: constants and `ocv()`.** At the top of the file put the constants below, then `ocv(soc)`, which converts a true charge (0 to 1) to a voltage using your
table. Below 0 % a real LFP cell collapses quickly, so we extend the table by one point at `soc = -0.05` with 2.0 V. That is what lets a deeply discharged cell trip the
under-voltage protection in Part 5.

```python
from __future__ import annotations

import math
import random

from chemistry import OCV_V, SOC_PCT, interp
from chip import MAX_CELLS, ChipReading

SCENARIOS = ("normal", "cell_drift", "overheat", "cloudy")

_SOC_EXT = [-0.05] + [p / 100.0 for p in SOC_PCT]     # 0..1 instead of percent, plus one point below 0
_OCV_EXT = [2.0] + OCV_V

PACK_EXTRA_R_OHM = 0.005           # busbars, fuse, connectors
THERMAL_MASS_J_PER_K = 4300.0      # heat capacity of the whole pack
COOLING_W_PER_K = 2.4              # time constant 4300 / 2.4 = about 30 simulated minutes
NORMAL_AMBIENT_C = 28.0
PEAK_SOLAR_C = 0.30                # panel: 0.3 C = 15 A for a 50 Ah battery
BASE_LOAD_C = 0.02                 # night-time load
EVENING_LOAD_C = 0.08              # extra load around 19:00
TAPER_BAND = 0.04                  # the charger tapers over the last 4 % of charge
BLEED_A = 0.05                     # balancing resistor current

DRIFT_CELL = 2                     # cell_drift: this cell leaks and its resistance grows
DRIFT_LEAK_A = 2.0
DRIFT_R_GROWTH_OHM_PER_H = 0.0005
OVERHEAT_RATE_C_PER_MIN = 0.25     # overheat: the surroundings heat up
OVERHEAT_MAX_AMBIENT_C = 60.0
CLOUD_FACTOR = 0.25                # cloudy: the panel delivers 25 % of its clear-sky current


def ocv(soc: float) -> float:
    ...
```

Note `PEAK_SOLAR_C` is a **C-rate**: a multiple of the battery capacity. "0.3 C" means 30 % of the capacity per hour, which is 15 A for 50 Ah.
Expressing loads as C-rates keeps the daily energy balance sensible whatever capacity you configure.

Test: `uv run pytest tests/test_battery.py -q -k ocv`.

**Step B: `__init__`.** Parameters: `cells=8, nominal_ah=50.0, true_capacity_factor=0.95, start_hour=8.0, seed=None`. Create:

- `self.rng = random.Random(seed)`. A private random generator. The same `seed` gives the same battery every run, which makes tests repeatable.
- `self.cap`: per-cell **true** capacity: `nominal_ah * true_capacity_factor * rng.uniform(0.985, 1.015)`.
- `self.r_base`: per-cell resistance `0.0010 * rng.uniform(0.9, 1.1)` ohm, and `self.r` a *copy* of it (scenarios modify `r`, `r_base` lets you undo).
- `self.soc`: per-cell true charge, `rng.uniform(0.55, 0.60)`.
- `sim_s` (simulated seconds since midnight: `start_hour * 3600`), `temp_c = 29.0`, `ambient_c`, `current_a = 0.0`, `scenario = "normal"`, `scenario_s = 0.0`, `cloud = 1.0` (the share of clear-sky solar: 1.0 is a clear sky).

Use list comprehensions: `[expr for _ in range(cells)]`. The order of the `rng` calls matters for identical numbers
(first `cap`, then `r_base`, then `soc`). If yours differ from the expected output later, that is the reason, and it is harmless.

**Step C: current and voltage.**

```python
    def hour_of_day(self) -> float: ...            # (sim_s / 3600) % 24

    def wanted_current(self) -> float:
        """Solar charge minus load.  + = charging."""

    def terminal_voltages(self, current_a: float) -> list[float]:
        """OCV(soc) + I * R for every cell."""
```

`wanted_current`: with `h = hour_of_day()` and `c = nominal_ah`,

- `solar = self.cloud * PEAK_SOLAR_C * c * sin(pi * (h - 6) / 12)` for `6 <= h <= 18`, else 0. The sine goes from 0 at 06:00 up to 1 at noon and back to 0 at 18:00. `self.cloud` is 1.0 on a clear day; step D explains the cloudy scenario.
- `evening = EVENING_LOAD_C * c * exp(-((h - 19) ** 2) / (2 * 1.5 ** 2))`. A bell curve (Gaussian) centred on 19:00, 1.5 h wide.
- return `solar - (BASE_LOAD_C * c + evening) + rng.gauss(0.0, 0.004 * c)`. The last term is sensor-like noise of 0.2 A.

Why does the noise stop mattering at full charge? Because step E multiplies the whole current by the taper factor, which is 0 when full. Think about why the
balancing logic in Part 5 needs that.

**Step D: scenarios.**

```python
    def set_scenario(self, name: str) -> None:       # validate, reset scenario_s, restore self.r from r_base, set self.cloud
    def _apply_scenario(self, dt: float) -> None:    # called each step
```

`_apply_scenario`: add `dt` to `scenario_s`. For `cell_drift`: the resistance of cell `DRIFT_CELL` grows linearly with the hours since the scenario began
(`r_base + DRIFT_R_GROWTH_OHM_PER_H * hours`) and its charge leaks away (`soc -= DRIFT_LEAK_A * dt / (cap * 3600)`, never below `-0.05`). For
`overheat`: the ambient temperature rises `OVERHEAT_RATE_C_PER_MIN` per minute of scenario time up to `OVERHEAT_MAX_AMBIENT_C`. In every other scenario the ambient
relaxes back to `NORMAL_AMBIENT_C` (move a fraction `min(1, dt / 1800)` of the way each step).

`cloudy` needs no code in `_apply_scenario`. Instead `set_scenario` sets `self.cloud` to `CLOUD_FACTOR` when the name is `"cloudy"` and to `1.0` for every other name, and
`wanted_current` multiplies the solar term by it. Clouds only shrink the sun; the night load is unchanged. For the default 50 Ah pack a clear day brings about 115 Ah of solar
against roughly 39 Ah of load. A cloudy day brings about 29 Ah, so the pack loses roughly 22 % of its charge every day. Because solar and load are both C-rates, this
holds whatever `nominal_ah` you configure: in this model a bigger battery does not ride out more cloudy days.

**Step E: `step()` and `read_chip()`.** This is the heart. `step(dt, chg_on, dis_on, bleed_mask)` does, in order:

1. `sim_s += dt`; `_apply_scenario(dt)`.
2. `i = wanted_current()`.
3. If `i > 0` (charging): multiply by `max(0, min(1, (1 - max(self.soc)) / TAPER_BAND))`, the taper, which is 1 until the fullest cell is within 4 % of full
   and then falls linearly to 0. If `chg_on` is false, `i = 0`. If `i <= 0` and `dis_on` is false, `i = 0`.
4. For every cell: `soc += i * dt / (cap * 3600)`, capped at 1.05. If bit `k` of `bleed_mask` is set, also subtract `BLEED_A * dt / (cap * 3600)`. Never go below `-0.05`.
5. Heat: `heat_w = i*i * (sum(r) + PACK_EXTRA_R_OHM)`, `cool_w = COOLING_W_PER_K * (temp_c - ambient_c)`, `temp_c += (heat_w - cool_w) * dt / THERMAL_MASS_J_PER_K`.
6. Remember `current_a = i` and return `read_chip()`.

`read_chip()` builds a `ChipReading`: the cell voltages from `terminal_voltages(current_a)`, **rounded to 3 decimals** (the chip's resolution) and padded with zeros up to
`MAX_CELLS`; the two cell temperatures as `temp_c` and `temp_c - 0.8` plus noise `rng.gauss(0, 0.15)`; the IC temperature `temp_c + 2`; and the MOSFET
temperature `temp_c + 4 * (current_a / 40) ** 2` (the switches warm up with current squared).

Bit tests: `bleed_mask >> k & 1` is 1 when bit `k` is set. Run all battery tests:

```bash
uv run pytest tests/test_battery.py -q
```

Expected: `16 passed`.

<details>
<summary>Solution: physical_battery.py (compare after your tests pass)</summary>

```python
"""The physical battery: chemistry, current, heat.  No firmware logic, no MQTT.

The firmware never sees this object directly, only the ChipReading made from it.
Keeping them apart is the whole point: the BMS reports what it BELIEVES.
"""
from __future__ import annotations

import math
import random

from chemistry import OCV_V, SOC_PCT, interp
from chip import MAX_CELLS, ChipReading

SCENARIOS = ("normal", "cell_drift", "overheat", "cloudy")

# OCV of one cell as a function of true charge (0..1).  Same shape as the firmware's table, but
# sorted by increasing charge, and extended a little BELOW 0 % where a real LFP cell collapses.
_SOC_EXT = [-0.05] + [p / 100.0 for p in SOC_PCT]
_OCV_EXT = [2.0] + OCV_V

PACK_EXTRA_R_OHM = 0.005
THERMAL_MASS_J_PER_K = 4300.0
COOLING_W_PER_K = 2.4              # time constant 4300 / 2.4 = about 30 simulated minutes
NORMAL_AMBIENT_C = 28.0
PEAK_SOLAR_C = 0.30                # panel: 0.3 C = 15 A for a 50 Ah battery
BASE_LOAD_C = 0.02                 # night-time load
EVENING_LOAD_C = 0.08              # extra load around 19:00
TAPER_BAND = 0.04                  # charger tapers its current over the last 4 % of charge
BLEED_A = 0.05                     # balancing resistor current

DRIFT_CELL = 2                     # cell_drift: this cell leaks and its resistance grows
DRIFT_LEAK_A = 2.0
DRIFT_R_GROWTH_OHM_PER_H = 0.0005
OVERHEAT_RATE_C_PER_MIN = 0.25     # overheat: the surroundings heat up
OVERHEAT_MAX_AMBIENT_C = 60.0
CLOUD_FACTOR = 0.25                # cloudy: the panel delivers 25 % of its clear-sky current


def ocv(soc: float) -> float:
    return interp(soc, _SOC_EXT, _OCV_EXT)


class Battery:
    def __init__(self, cells: int = 8, nominal_ah: float = 50.0, true_capacity_factor: float = 0.95,
                 start_hour: float = 8.0, seed: int | None = None):
        assert 1 <= cells <= MAX_CELLS
        self.n = cells
        self.nominal_ah = nominal_ah
        self.rng = random.Random(seed)
        # The cells are 5 % smaller than the label says; the firmware does not know that.
        self.cap = [nominal_ah * true_capacity_factor * self.rng.uniform(0.985, 1.015)
                    for _ in range(cells)]
        self.r_base = [0.0010 * self.rng.uniform(0.9, 1.1) for _ in range(cells)]
        self.r = list(self.r_base)
        self.soc = [self.rng.uniform(0.55, 0.60) for _ in range(cells)]   # TRUE charge
        self.sim_s = start_hour * 3600.0
        self.temp_c = 29.0
        self.ambient_c = NORMAL_AMBIENT_C
        self.current_a = 0.0
        self.scenario = "normal"
        self.scenario_s = 0.0
        self.cloud = 1.0               # share of clear-sky solar that reaches the panel (1.0 = clear sky)

    # ------------------------------------------------------------------ helpers
    def hour_of_day(self) -> float:
        return (self.sim_s / 3600.0) % 24.0

    def wanted_current(self) -> float:
        """Solar charge minus load.  + = charging."""
        h = self.hour_of_day()
        c = self.nominal_ah            # loads scale with the battery: C-rates, not amps
        solar = (self.cloud * PEAK_SOLAR_C * c * math.sin(math.pi * (h - 6.0) / 12.0)
                 if 6.0 <= h <= 18.0 else 0.0)
        evening = EVENING_LOAD_C * c * math.exp(-((h - 19.0) ** 2) / (2 * 1.5 ** 2))
        return solar - (BASE_LOAD_C * c + evening) + self.rng.gauss(0.0, 0.004 * c)

    def terminal_voltages(self, current_a: float) -> list[float]:
        return [ocv(s) + current_a * r for s, r in zip(self.soc, self.r)]

    def set_scenario(self, name: str) -> None:
        if name not in SCENARIOS:
            raise ValueError(f"unknown scenario {name!r}; choose from {SCENARIOS}")
        self.scenario, self.scenario_s = name, 0.0
        self.r = list(self.r_base)
        self.cloud = CLOUD_FACTOR if name == "cloudy" else 1.0

    def _apply_scenario(self, dt: float) -> None:
        self.scenario_s += dt
        if self.scenario == "cell_drift":
            hours = self.scenario_s / 3600.0
            k = DRIFT_CELL
            self.r[k] = self.r_base[k] + DRIFT_R_GROWTH_OHM_PER_H * hours
            self.soc[k] = max(-0.05, self.soc[k] - DRIFT_LEAK_A * dt / (self.cap[k] * 3600.0))
        if self.scenario == "overheat":
            rise = OVERHEAT_RATE_C_PER_MIN * self.scenario_s / 60.0
            self.ambient_c = min(OVERHEAT_MAX_AMBIENT_C, NORMAL_AMBIENT_C + rise)
        else:
            self.ambient_c += (NORMAL_AMBIENT_C - self.ambient_c) * min(1.0, dt / 1800.0)

    # ------------------------------------------------------------------ physics
    def step(self, dt: float, chg_on: bool, dis_on: bool, bleed_mask: int) -> ChipReading:
        """Advance dt seconds.  `chg_on`/`dis_on` are the BMS's MOSFET switches."""
        self.sim_s += dt
        self._apply_scenario(dt)

        i = self.wanted_current()
        if i > 0:
            # the charger tapers as the fullest cell approaches 100 %
            i *= max(0.0, min(1.0, (1.0 - max(self.soc)) / TAPER_BAND))
            if not chg_on:
                i = 0.0
        elif not dis_on:
            i = 0.0

        for k in range(self.n):                                    # coulomb counting, true capacity
            self.soc[k] = min(1.05, self.soc[k] + i * dt / (self.cap[k] * 3600.0))
            if bleed_mask >> k & 1:
                self.soc[k] -= BLEED_A * dt / (self.cap[k] * 3600.0)
            self.soc[k] = max(-0.05, self.soc[k])

        heat_w = i * i * (sum(self.r) + PACK_EXTRA_R_OHM)           # I^2 R heating
        cool_w = COOLING_W_PER_K * (self.temp_c - self.ambient_c)
        self.temp_c += (heat_w - cool_w) * dt / THERMAL_MASS_J_PER_K
        self.current_a = i
        return self.read_chip()

    def read_chip(self) -> ChipReading:
        """What the BQ76952 would measure right now (with a little sensor noise)."""
        v = self.terminal_voltages(self.current_a)
        noise = self.rng.gauss
        v_pad = [round(x, 3) for x in v] + [0.0] * (MAX_CELLS - self.n)
        return ChipReading(
            cell_v=v_pad,
            current_a=self.current_a,
            cell_temps_c=[self.temp_c + noise(0, 0.15), self.temp_c - 0.8 + noise(0, 0.15)],
            ic_temp_c=self.temp_c + 2.0,
            mosfet_temp_c=self.temp_c + 4.0 * (self.current_a / 40.0) ** 2,
        )
```

Look at three design choices. (1) The class never mentions MQTT or the firmware. (2) `step()` takes the switch states and bleed mask as *inputs*, so the control loop lives outside. (3)
The taper multiplies noise too, so a full pack has *exactly* zero current. Without that the simulated current would never be "idle" and balancing could never start.

</details>

### 4.5 Look at a day: `dev_battery.py`

In the service folder (not in `tests/`) create `dev_battery.py`:

```python
"""Part 4: run the bare battery for two days with both switches closed (no firmware yet)."""
from physical_battery import Battery

b = Battery(seed=1, start_hour=0.0)
print(" hour   I(A)  true_soc%  cell_min  cell_max  temp_C")
for step in range(48 * 6):                         # 10-minute steps for two days
    reading = b.step(600, chg_on=True, dis_on=True, bleed_mask=0)
    if step % 18 == 17 and step < 24 * 6:          # print every 3 hours of day one
        cells = [v for v in reading.cell_v if v > 0.5]
        print(f"{b.hour_of_day():5.1f} {b.current_a:6.1f} {100 * sum(b.soc) / b.n:9.1f}"
              f" {min(cells):9.3f} {max(cells):9.3f} {b.temp_c:7.1f}")
```

Run `uv run python dev_battery.py`. With the reference code the result is:

```text
 hour   I(A)  true_soc%  cell_min  cell_max  temp_C
  3.0   -1.0      51.2     3.264     3.266    28.0
  6.0   -0.8      44.1     3.262     3.264    28.0
  9.0    9.1      75.2     3.300     3.309    28.4
 12.0    0.0      98.2     3.328     3.392    28.0
 15.0    0.0      98.2     3.328     3.392    28.0
 18.0   -4.3      94.0     3.306     3.326    28.1
 21.0   -2.5      67.0     3.272     3.279    28.1
  0.0   -0.8      58.1     3.268     3.270    28.0
```

Check the *shape*, not the digits:

- Before 06:00 and after 18:00 the current is slightly negative (the night load) and the charge falls.
- At 09:00 the current is positive and the charge climbs quickly.
- At 12:00 and 15:00 the current is exactly 0.0, the charge stands at about 98 %, and **`cell_min` and `cell_max` differ by about 65 mV**. That is the fullest cell
  limiting the charger while the others are still lower, with the average at 98 % rather than 100 %. Balancing (Part 5) is what will close that gap.
- The temperature barely moves from 28 °C because the currents are small. We will push it with the `overheat` scenario in Part 9.

## Your turn

1. Change `start_hour` to 0 and `seed` to 2. Does the daily shape stay the same? Which numbers change?
2. Set `true_capacity_factor=1.0`. Compare the charge in the last line (midnight, after the whole day) with before. What changed, and why?
3. Add a line printing the **imbalance** (`cell_max - cell_min`) in millivolts. At which hour is it largest, and why is the middle of the OCV curve a bad place to read it?
4. Right after creating the battery in `dev_battery.py`, call `b.set_scenario("cloudy")` and run it again. Does the pack still reach about 98 % at midday? How low is the charge at midnight? Then let the loop run for four days instead of two: what happens?

<details>
<summary>Hints</summary>

1. The sunshine hours and the load times are the same, so the shape is. Initial charge, resistances and noise change.
2. It ends higher (about 59.9 % instead of 58.1 %). Bigger cells lose a smaller percentage to the same night load. The firmware's estimate in Part 5 will also be more accurate, because its idea of the capacity (50 Ah) is then right.
3. When the fullest cell is at the steep end of the curve, a small difference in charge means a big difference in voltage. In the flat middle a large charge difference is only a few millivolts, so voltage hides imbalance there.
4. No. With the reference code the charge only climbs to about 78 % at 15:00 and day one ends near 35 % at midnight (58 % with clear skies). Midnight values on the following days are about 11 %, then the cells hit the floor of -5 %: with both switches closed nothing stops them, while in Part 5 the firmware will open the switches long before that. The current also never settles at zero the way it does on a clear afternoon, so the 30-minute idle rule that triggers balancing in Part 5 would not be met either.

</details>

## Checkpoint

- `uv run pytest tests/test_chemistry.py tests/test_battery.py -q` shows `21 passed`.
- `dev_battery.py` prints a plausible day.
- You can explain in one sentence each: coulomb counting, OCV, why current makes the terminal voltage differ from OCV, why the charger tapers.

## Commit

```bash
git add services/bms-simulator
git commit -m "feat(bms-simulator): physical LFP battery model"
```

## Self-check questions

1. A 50 Ah battery is charged with 10 A for 3 hours. By how many percent does `soc` rise (ignoring the 5 % smaller true capacity)?
2. Why does `Battery` keep both `r_base` and `r`?
3. What would go wrong if `step()` called `wanted_current()` twice?

<details>
<summary>Answers</summary>

1. 30 Ah of 50 Ah is 60 %.
2. Scenarios change `r` over time. `r_base` lets `set_scenario` restore the healthy values so you can switch back to `normal`.
3. The random noise would be drawn twice, and the current used for charge, heat and the reading could disagree. Compute it once, store it, and use that value everywhere.

</details>

Next: [Part 5: The firmware layer](05-firmware-layer.md).
