"""Physics-light LiFePO4 battery model. No MQTT / web code in here.

Sign convention (same as the Libre Solar firmware): pack current > 0 = charging.
Field names mirror the ThingSet `Meas` group of bms-firmware.
"""
from __future__ import annotations

import math
import random
import threading

# Error flag names = bits of BMS_ERR_* in bms-firmware/include/bms/bms_common.h
ERR_CELL_UNDERVOLTAGE = "CELL_UNDERVOLTAGE"
ERR_CELL_OVERVOLTAGE = "CELL_OVERVOLTAGE"
ERR_DIS_OVERCURRENT = "DIS_OVERCURRENT"
ERR_CHG_OVERCURRENT = "CHG_OVERCURRENT"
ERR_DIS_OVERTEMP = "DIS_OVERTEMP"
ERR_CHG_OVERTEMP = "CHG_OVERTEMP"
ERR_CELL_FAILURE = "CELL_FAILURE"

# LFP open-circuit voltage curve: flat in the middle, steep at the ends
OCV_SOC = [0.00, 0.05, 0.10, 0.20, 0.50, 0.80, 0.90, 0.95, 1.00]
OCV_V = [2.90, 3.10, 3.20, 3.25, 3.30, 3.33, 3.38, 3.45, 3.60]

# Protection limits (also published in the `info` topic)
LIMITS = {
    "cell_overvoltage_v": 3.65,
    "cell_undervoltage_v": 2.80,
    "chg_max_temp_c": 45.0,
    "dis_max_temp_c": 55.0,
    "temp_hysteresis_c": 3.0,
    "cell_failure_diff_v": 0.20,
    "chg_overcurrent_a": 60.0,
    "dis_overcurrent_a": 80.0,
}

SCENARIOS = ("normal", "cell_drift", "overheat")


def interp(x: float, xs: list[float], ys: list[float]) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            f = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + f * (ys[i] - ys[i - 1])
    return ys[-1]


class BatteryModel:
    def __init__(self, cells: int = 8, capacity_ah: float = 100.0,
                 scenario: str = "normal", start_hour: float = 8.0, seed: int | None = None):
        self.n = cells
        self.capacity_ah = capacity_ah
        self._rng = random.Random(seed)
        self._lock = threading.Lock()

        # Slight manufacturing spread so cells drift apart naturally
        self.cell_cap = [capacity_ah * self._rng.uniform(0.985, 1.015) for _ in range(cells)]
        self.cell_r = [0.0010 * self._rng.uniform(0.9, 1.1) for _ in range(cells)]  # ohm
        self.cell_soc = [self._rng.uniform(0.55, 0.60) for _ in range(cells)]

        self.sim_s = start_hour * 3600.0
        self.scenario_s = 0.0          # sim seconds since scenario was selected
        self.ambient_c = 28.0
        self.temp_c = 29.0
        self.current_a = 0.0
        self.ah_throughput = 0.0
        self.errors: set[str] = set()
        self.balancing = [False] * cells
        self.scenario = "normal"
        self.set_scenario(scenario)

    # ------------------------------------------------------------------ control
    def set_scenario(self, name: str) -> None:
        if name not in SCENARIOS:
            raise ValueError(f"unknown scenario {name!r}; choose from {SCENARIOS}")
        with self._lock:
            self.scenario = name
            self.scenario_s = 0.0
            # reset effects of previous scenarios
            self.cell_r = [min(r, 0.0012) for r in self.cell_r]

    # ------------------------------------------------------------------ physics
    def _cell_voltages(self, current: float) -> list[float]:
        return [interp(s, OCV_SOC, OCV_V) + current * r for s, r in zip(self.cell_soc, self.cell_r)]

    def _target_current(self) -> float:
        h = (self.sim_s / 3600.0) % 24.0
        solar = 40.0 * max(0.0, math.sin(math.pi * (h - 6.0) / 12.0)) ** 1.5 if 6 <= h <= 18 else 0.0
        evening = 12.0 * math.exp(-((h - 19.5) ** 2) / (2 * 2.0 ** 2))
        load = 2.5 + evening + (3.0 if 6 <= h <= 9 else 0.0)
        return solar - load + self._rng.gauss(0, 0.6)

    def _step_once(self, dt: float) -> None:
        self.sim_s += dt
        self.scenario_s += dt
        hrs = self.scenario_s / 3600.0

        if self.scenario == "cell_drift":
            # one weak cell: resistance grows, capacity shrinks
            self.cell_r[2] = 0.0012 + 0.0012 * hrs
            self.cell_cap[2] = max(self.capacity_ah * 0.80, self.cell_cap[2] - 0.05 * dt / 3600)
        if self.scenario == "overheat":
            # cooling failure: ambient (enclosure) climbs ~0.25 degC per simulated minute
            self.ambient_c = min(60.0, 28.0 + 0.25 * self.scenario_s / 60.0)
        else:
            self.ambient_c += (28.0 - self.ambient_c) * min(1.0, dt / 1800.0)

        # --- desired current, then protections / taper -------------------------
        i = self._target_current()
        vmax = max(self._cell_voltages(i))
        if i > 0:
            soc_max = max(self.cell_soc)
            i *= max(0.0, min(1.0, (1.0 - soc_max) / 0.04))   # CV-like taper near full
            if vmax > LIMITS["cell_overvoltage_v"]:
                i = 0.0
            if ERR_CHG_OVERTEMP in self.errors:
                i = 0.0
        else:
            if min(self._cell_voltages(i)) < LIMITS["cell_undervoltage_v"]:
                i = 0.0
            if ERR_DIS_OVERTEMP in self.errors:
                i = 0.0
        self.current_a = i

        # --- coulomb counting per cell ----------------------------------------
        for k in range(self.n):
            self.cell_soc[k] += i * dt / (self.cell_cap[k] * 3600.0)
        self.ah_throughput += abs(i) * dt / 3600.0

        # --- passive balancing (stretch goal from the learning path) ----------
        v = self._cell_voltages(i)
        vmin = min(v)
        self.balancing = [(i >= 0 and vmax > 3.40 and x > vmin + 0.015) for x in v]
        for k, b in enumerate(self.balancing):
            if b:
                self.cell_soc[k] -= 0.1 * dt / (self.cell_cap[k] * 3600.0)  # 100 mA bleed
        self.cell_soc = [min(1.0, max(0.0, s)) for s in self.cell_soc]

        # --- thermal: first-order ---------------------------------------------
        r_pack = sum(self.cell_r) + 0.005
        heat_w = i * i * r_pack
        k_cool, c_th = 2.4, 4300.0     # W/K, J/K  (tau ~ 30 min)
        self.temp_c += (heat_w - k_cool * (self.temp_c - self.ambient_c)) * dt / c_th

        self._update_errors(i, v)

    def _update_errors(self, i: float, v: list[float]) -> None:
        e = self.errors
        hyst = LIMITS["temp_hysteresis_c"]

        def latch(name: str, trip: bool, clear: bool) -> None:
            if trip:
                e.add(name)
            elif clear:
                e.discard(name)

        latch(ERR_CELL_OVERVOLTAGE, max(v) > LIMITS["cell_overvoltage_v"], max(v) < 3.55)
        latch(ERR_CELL_UNDERVOLTAGE, min(v) < LIMITS["cell_undervoltage_v"], min(v) > 3.0)
        latch(ERR_CHG_OVERTEMP, self.temp_c > LIMITS["chg_max_temp_c"],
              self.temp_c < LIMITS["chg_max_temp_c"] - hyst)
        latch(ERR_DIS_OVERTEMP, self.temp_c > LIMITS["dis_max_temp_c"],
              self.temp_c < LIMITS["dis_max_temp_c"] - hyst)
        latch(ERR_CELL_FAILURE, max(v) - min(v) > LIMITS["cell_failure_diff_v"],
              max(v) - min(v) < LIMITS["cell_failure_diff_v"] * 0.7)
        latch(ERR_CHG_OVERCURRENT, i > LIMITS["chg_overcurrent_a"], i < LIMITS["chg_overcurrent_a"] * 0.9)
        latch(ERR_DIS_OVERCURRENT, -i > LIMITS["dis_overcurrent_a"], -i < LIMITS["dis_overcurrent_a"] * 0.9)

    def step(self, dt_sim_s: float) -> None:
        """Advance the model by dt_sim_s simulated seconds."""
        with self._lock:
            while dt_sim_s > 0:
                chunk = min(30.0, dt_sim_s)     # keep explicit Euler stable
                self._step_once(chunk)
                dt_sim_s -= chunk

    # ------------------------------------------------------------------ output
    def snapshot(self) -> dict:
        """Raw measurements, the same shape the real BMS would publish."""
        with self._lock:
            i = self.current_a
            v = self._cell_voltages(i)
            chg_err = {ERR_CHG_OVERTEMP, ERR_CELL_OVERVOLTAGE, ERR_CHG_OVERCURRENT, ERR_CELL_FAILURE}
            dis_err = {ERR_DIS_OVERTEMP, ERR_CELL_UNDERVOLTAGE, ERR_DIS_OVERCURRENT, ERR_CELL_FAILURE}
            chg_ok = not (self.errors & chg_err)
            dis_ok = not (self.errors & dis_err)
            state = "NORMAL" if chg_ok and dis_ok else "DIS" if dis_ok else "CHG" if chg_ok else "OFF"
            rnd = self._rng.gauss
            return {
                "state": state,
                "pack_voltage_v": round(sum(v), 2),
                "pack_current_a": round(i, 2),
                "soc_pct": round(100 * sum(self.cell_soc) / self.n, 1),
                "cell_voltages_v": [round(x, 3) for x in v],
                "cell_temps_c": [round(self.temp_c + rnd(0, 0.15), 1), round(self.temp_c - 0.8 + rnd(0, 0.15), 1)],
                "mosfet_temp_c": round(self.temp_c + 4 * (i / 40.0) ** 2, 1),
                "balancing_mask": sum(1 << k for k, b in enumerate(self.balancing) if b),
                "errors": sorted(self.errors),
            }

    def debug(self) -> dict:
        with self._lock:
            return {
                "scenario": self.scenario,
                "sim_hour": round((self.sim_s / 3600.0) % 24, 2),
                "ambient_c": round(self.ambient_c, 1),
                "equivalent_cycles": round(self.ah_throughput / (2 * self.capacity_ah), 3),
            }
