import json

from jinja2 import Environment

from ha_discovery import discovery_messages


def _render(template: str, meas: dict) -> str:
    return Environment().from_string(template).render(value_json=meas)


def test_every_entity_has_a_unique_id_and_topic():
    msgs = list(discovery_messages("dev1", "bms/dev1", 8))
    topics = [t for t, _ in msgs]
    ids = [p["unique_id"] for _, p in msgs]
    assert len(topics) == len(set(topics))
    assert len(ids) == len(set(ids))
    assert len(msgs) == 9 + 1 + 1 + 1 + 8   # sensors, state, faults, binary sensor, cells
    for _, payload in msgs:
        json.dumps(payload)                  # must be serialisable


def test_templates_compute_what_they_claim():
    meas = {"soc_pct": 71.5, "pack_voltage_v": 26.0, "pack_current_a": -10.0,
            "cell_voltages_v": [3.30, 3.31, 3.29], "cell_temps_c": [28.0, 30.5],
            "mosfet_temp_c": 33.0, "state": "NORMAL", "errors": []}
    by_key = {p["unique_id"]: p for _, p in discovery_messages("d", "bms/d", 3)}
    assert _render(by_key["d_soc"]["value_template"], meas) == "71.5"
    assert _render(by_key["d_pack_power"]["value_template"], meas) == "-260.0"
    assert _render(by_key["d_cell_temp_max"]["value_template"], meas) == "30.5"
    assert _render(by_key["d_imbalance"]["value_template"], meas) == "20.0"
    assert _render(by_key["d_cell2_v"]["value_template"], meas) == "3.31"
    assert _render(by_key["d_faults"]["value_template"], meas) == "none"
    assert _render(by_key["d_fault"]["value_template"], meas) == "OFF"
    meas["errors"] = ["CHG_OVERTEMP", "CELL_FAILURE"]
    assert _render(by_key["d_faults"]["value_template"], meas) == "CHG_OVERTEMP, CELL_FAILURE"
    assert _render(by_key["d_fault"]["value_template"], meas) == "ON"
