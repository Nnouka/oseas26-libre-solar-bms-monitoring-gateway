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
