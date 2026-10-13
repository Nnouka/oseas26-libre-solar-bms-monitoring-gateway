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
