"""Home Assistant entities for the values only the bridge can compute.

They attach to the same HA device as the simulator's entities (same identifiers),
so everything shows up on one device page.
"""
from __future__ import annotations

PREFIX = "homeassistant"


def messages(device_id: str) -> list[tuple[str, dict]]:
    base = f"bms/{device_id}"
    common = {
        "state_topic": f"{base}/summary",
        "availability_topic": f"{base}/status",
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": {"identifiers": [device_id]},
    }

    def sensor(key, name, template, unit=None, icon=None, state_class=None, prec=None):
        p = {**common, "name": name, "unique_id": f"{device_id}_{key}", "value_template": template}
        if unit:
            p["unit_of_measurement"] = unit
        if icon:
            p["icon"] = icon
        if state_class:
            p["state_class"] = state_class
        if prec is not None:
            p["suggested_display_precision"] = prec
        return f"{PREFIX}/sensor/{device_id}/{key}/config", p

    return [
        sensor("health", "Battery health", "{{ value_json.health }}", icon="mdi:heart-pulse"),
        sensor("health_score", "Health score", "{{ value_json.health_score }}", None,
               "mdi:gauge", "measurement", 0),
        sensor("health_reasons", "Health reasons",
               "{{ value_json.health_reasons | join('; ') if value_json.health_reasons | length > 0 else 'all good' }}",
               icon="mdi:clipboard-text-outline"),
        sensor("soh", "State of health (estimated)", "{{ value_json.soh_pct }}", "%",
               "mdi:battery-heart-variant", "measurement", 1),
        sensor("cycles", "Equivalent full cycles", "{{ value_json.cycles }}",
               icon="mdi:battery-sync", state_class="total_increasing", prec=2),
    ]
