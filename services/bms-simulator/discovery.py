"""Home Assistant MQTT auto-discovery payloads.

Lives in its own module so it can move from the simulator into the bridge
service later without touching the battery model.
"""
from __future__ import annotations

DISCOVERY_PREFIX = "homeassistant"


def _device(device_id: str) -> dict:
    return {
        "identifiers": [device_id],
        "name": f"Libre Solar BMS {device_id}",
        "manufacturer": "Libre Solar",
        "model": "BMS C1",
    }


def discovery_messages(device_id: str, base: str, cells: int) -> list[tuple[str, dict]]:
    """Return [(config_topic, payload), ...] for every HA entity."""
    meas = f"{base}/meas"
    common = {
        "state_topic": meas,
        "availability_topic": f"{base}/status",
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": _device(device_id),
        "expire_after": 120,   # entity goes 'unavailable' if no data for 2 min
    }

    def sensor(key, name, template, unit=None, dev_class=None, icon=None, state_class="measurement", prec=None):
        p = {**common, "name": name, "unique_id": f"{device_id}_{key}", "value_template": template}
        if unit:
            p["unit_of_measurement"] = unit
        if dev_class:
            p["device_class"] = dev_class
        if icon:
            p["icon"] = icon
        if state_class:
            p["state_class"] = state_class
        if prec is not None:
            p["suggested_display_precision"] = prec
        return (f"{DISCOVERY_PREFIX}/sensor/{device_id}/{key}/config", p)

    msgs = [
        sensor("soc", "State of charge", "{{ value_json.soc_pct }}", "%", "battery", prec=1),
        sensor("pack_voltage", "Pack voltage", "{{ value_json.pack_voltage_v }}", "V", "voltage", prec=2),
        sensor("pack_current", "Pack current", "{{ value_json.pack_current_a }}", "A", "current", prec=1),
        sensor("power", "Pack power", "{{ (value_json.pack_voltage_v * value_json.pack_current_a) | round(0) }}", "W", "power", prec=0),
        sensor("temp_max", "Max cell temperature", "{{ value_json.cell_temps_c | max }}", "°C", "temperature", prec=1),
        sensor("mosfet_temp", "MOSFET temperature", "{{ value_json.mosfet_temp_c }}", "°C", "temperature", prec=1),
        sensor("cell_min", "Min cell voltage", "{{ value_json.cell_voltages_v | min }}", "V", "voltage", prec=3),
        sensor("cell_max", "Max cell voltage", "{{ value_json.cell_voltages_v | max }}", "V", "voltage", prec=3),
        sensor("imbalance", "Cell imbalance",
               "{{ ((value_json.cell_voltages_v | max) - (value_json.cell_voltages_v | min)) | round(3) }}",
               "V", "voltage", "mdi:scale-unbalanced", prec=3),
        sensor("bms_state", "BMS state", "{{ value_json.state }}", icon="mdi:battery-heart", state_class=None),
        sensor("errors", "Active faults",
               "{{ value_json.errors | join(', ') if value_json.errors | length > 0 else 'none' }}",
               icon="mdi:alert-circle-outline", state_class=None),
    ]
    for i in range(cells):
        msgs.append(sensor(f"cell_{i + 1}", f"Cell {i + 1} voltage",
                           "{{ value_json.cell_voltages_v[%d] }}" % i, "V", "voltage", prec=3))

    fault = {**common, "name": "Fault", "unique_id": f"{device_id}_fault", "device_class": "problem",
             "value_template": "{{ 'ON' if value_json.errors | length > 0 else 'OFF' }}",
             "payload_on": "ON", "payload_off": "OFF"}
    msgs.append((f"{DISCOVERY_PREFIX}/binary_sensor/{device_id}/fault/config", fault))
    return msgs
