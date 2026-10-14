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
