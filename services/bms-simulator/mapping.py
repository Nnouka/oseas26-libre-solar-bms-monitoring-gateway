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
