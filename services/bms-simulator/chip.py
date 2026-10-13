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
