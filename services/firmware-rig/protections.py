"""The BQ76952's own protection functions, which native_sim's memory-only emulator does not have.

On real hardware the chip trips these by itself and the firmware only READS SAFETY_STATUS A/B/C
(`bq769x2.c`, lines ~800-820). The rig has to play the chip here. Limits are the LFP defaults of the
firmware's `bms_init_config()`; delays (2 s, 320 ms) are not modelled, and the die/MOSFET limits are
the rig's own approximations. This is a model of the protection, not a validation of it.
"""
from __future__ import annotations

from chip import NOT_CONNECTED_V, ChipReading

NO_SENSOR_C = -200.0            # a missing thermistor reads -273 K


class ChipProtections:
    def __init__(self, capacity_ah: float = 50.0, cell_ov: float = 3.80, cell_ov_reset: float = 3.40,
                 cell_uv: float = 2.50, cell_uv_reset: float = 3.10, chg_ot: float = 45.0, dis_ot: float = 45.0,
                 chg_ut: float = 0.0, dis_ut: float = -20.0, temp_hyst: float = 5.0,
                 int_ot: float = 85.0, fet_ot: float = 100.0):
        self.__dict__.update(locals())
        self._tripped: set[str] = set()

    def _latch(self, name: str, trip: bool, clear: bool) -> None:
        """Set when `trip`; release only once `clear` (hysteresis)."""
        if trip:
            self._tripped.add(name)
        elif clear:
            self._tripped.discard(name)

    def update(self, r: ChipReading) -> tuple[int, int, int]:
        """(SAFETY_STATUS_A, SAFETY_STATUS_B, SAFETY_STATUS_C) for this reading."""
        cells = [v for v in r.cell_v if v > NOT_CONNECTED_V]
        if cells:
            hi, lo = max(cells), min(cells)
            self._latch("COV", hi > self.cell_ov, hi < self.cell_ov_reset)
            self._latch("CUV", lo < self.cell_uv, lo > self.cell_uv_reset)
        temps = [t for t in r.cell_temps_c if t > NO_SENSOR_C]
        if temps:
            t_hi, t_lo, h = max(temps), min(temps), self.temp_hyst
            self._latch("OTC", t_hi > self.chg_ot, t_hi < self.chg_ot - h)
            self._latch("OTD", t_hi > self.dis_ot, t_hi < self.dis_ot - h)
            self._latch("UTC", t_lo < self.chg_ut, t_lo > self.chg_ut + h)
            self._latch("UTD", t_lo < self.dis_ut, t_lo > self.dis_ut + h)
        if r.ic_temp_c > NO_SENSOR_C:
            self._latch("OTINT", r.ic_temp_c > self.int_ot, r.ic_temp_c < self.int_ot - self.temp_hyst)
        if r.mosfet_temp_c > NO_SENSOR_C:
            self._latch("OTF", r.mosfet_temp_c > self.fet_ot, r.mosfet_temp_c < self.fet_ot - self.temp_hyst)
        i, cap = r.current_a, self.capacity_ah
        self._latch("OCC", i > cap, i < 0.9 * cap)
        self._latch("OCD1", -i > cap, -i < 0.9 * cap)
        self._latch("SCD", -i > 2 * cap, -i < cap)
        return self._byte_a(), self._byte_b(), 0

    def _bit(self, name: str, bit: int) -> int:
        return (1 << bit) if name in self._tripped else 0

    def _byte_a(self) -> int:      # union bq769x2_reg_safety_a
        return (self._bit("CUV", 2) | self._bit("COV", 3) | self._bit("OCC", 4)
                | self._bit("OCD1", 5) | self._bit("SCD", 7))

    def _byte_b(self) -> int:      # union bq769x2_reg_safety_b
        return (self._bit("UTC", 0) | self._bit("UTD", 1) | self._bit("OTC", 4)
                | self._bit("OTD", 5) | self._bit("OTINT", 6) | self._bit("OTF", 7))
