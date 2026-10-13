"""Home Assistant MQTT discovery: tell HA which entities exist, over MQTT itself."""
from __future__ import annotations

DISCOVERY_PREFIX = "homeassistant"   # Home Assistant's default; must match its MQTT settings

# (key, display name, Jinja template evaluated on the meas JSON, unit, device_class)
SENSORS = [
    ("soc", "State of charge", "{{ value_json.soc_pct }}", "%", "battery"),
    ("pack_voltage", "Pack voltage", "{{ value_json.pack_voltage_v }}", "V", "voltage"),
    ("pack_current", "Pack current", "{{ value_json.pack_current_a }}", "A", "current"),
    ("pack_power", "Pack power",
     "{{ (value_json.pack_voltage_v * value_json.pack_current_a) | round(1) }}", "W", "power"),
    ("cell_temp_max", "Hottest cell", "{{ value_json.cell_temps_c | max }}", "°C", "temperature"),
    ("mosfet_temp", "MOSFET temperature", "{{ value_json.mosfet_temp_c }}", "°C", "temperature"),
    ("cell_v_min", "Lowest cell voltage", "{{ value_json.cell_voltages_v | min }}", "V", "voltage"),
    ("cell_v_max", "Highest cell voltage", "{{ value_json.cell_voltages_v | max }}", "V", "voltage"),
    ("imbalance", "Cell imbalance",
     "{{ (((value_json.cell_voltages_v | max) - (value_json.cell_voltages_v | min)) * 1000) | round(0) }}",
     "mV", "voltage"),
]


def _common(device_id: str, base: str, expire_after: int) -> dict:
    """Keys shared by every entity: where the data comes from and which device owns it."""
    return {
        "state_topic": f"{base}/meas",
        "availability_topic": f"{base}/status",
        "payload_available": "online",
        "payload_not_available": "offline",
        "expire_after": expire_after,      # entity becomes "unavailable" if no message for this long
        "device": {
            "identifiers": [device_id],
            "name": f"BMS {device_id}",
            "manufacturer": "Libre Solar",
            "model": "BMS C1 (simulated)",
        },
    }


def discovery_messages(device_id: str, base: str, n_cells: int, expire_after: int = 120):
    """Yield (topic, payload) pairs, one per Home Assistant entity."""
    common = _common(device_id, base, expire_after)

    for key, name, template, unit, device_class in SENSORS:
        yield (f"{DISCOVERY_PREFIX}/sensor/{device_id}/{key}/config", {
            **common,
            "name": name,
            "unique_id": f"{device_id}_{key}",
            "value_template": template,
            "unit_of_measurement": unit,
            "device_class": device_class,
            "state_class": "measurement",
        })

    yield (f"{DISCOVERY_PREFIX}/sensor/{device_id}/state/config", {
        **common,
        "name": "BMS state",
        "unique_id": f"{device_id}_state",
        "value_template": "{{ value_json.state }}",
    })

    yield (f"{DISCOVERY_PREFIX}/sensor/{device_id}/faults/config", {
        **common,
        "name": "Active faults",
        "unique_id": f"{device_id}_faults",
        "value_template": "{{ value_json.errors | join(', ') if value_json.errors else 'none' }}",
    })

    yield (f"{DISCOVERY_PREFIX}/binary_sensor/{device_id}/fault/config", {
        **common,
        "name": "Fault",
        "unique_id": f"{device_id}_fault",
        "value_template": "{{ 'ON' if value_json.errors | length > 0 else 'OFF' }}",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device_class": "problem",
    })

    for k in range(n_cells):
        yield (f"{DISCOVERY_PREFIX}/sensor/{device_id}/cell{k + 1}_v/config", {
            **common,
            "name": f"Cell {k + 1} voltage",
            "unique_id": f"{device_id}_cell{k + 1}_v",
            "value_template": f"{{{{ value_json.cell_voltages_v[{k}] }}}}",
            "unit_of_measurement": "V",
            "device_class": "voltage",
            "state_class": "measurement",
            "entity_category": "diagnostic",
        })
