# Part 8: Home Assistant

**Goal.** Make Home Assistant (HA) discover the simulated battery by itself: a device with a dozen sensors, a fault indicator, and cell voltages, all created from MQTT messages
that your simulator publishes. No clicking, no YAML in HA.

**Time.** 1.5 hours (most of it is the first HA start).

> **Production parity.** In the final stack the *gateway* publishes these discovery messages (it has the same `discovery.py`), because a real BMS does not know about Home Assistant. You write the code here once and it moves.

## Ideas

**MQTT discovery.** HA listens on `homeassistant/<component>/<object_id>/config`. A retained JSON message there says "I am a sensor named X, my value is on topic Y, here is how to extract it". HA then creates the entity. When the
device vanishes its `availability_topic` (our `status` topic with its last will) turns the entities *unavailable* in the interface rather than leaving stale numbers.

**One device, many entities.** All entities share a `device` block with the same `identifiers`, so HA groups them as one device ("BMS haske-bms-001").

**`value_template`.** Our `meas` message is one JSON object. Each entity picks its value with a small Jinja template: `{{ value_json.soc_pct }}`. Templates can compute, too: power is `{{ (value_json.pack_voltage_v * value_json.pack_current_a) | round(1) }}`.

**`unique_id`.** Must never change and never repeat. HA stores entity history under it.

**`expire_after`.** If no `meas` message arrives within this many seconds the entity turns unavailable. This catches a *frozen* simulator that is still connected, which the last will cannot.

**Device classes.** `battery`, `voltage`, `current`, `power`, `temperature` give HA the right icons, units conversion and graphs. `state_class: measurement` makes HA record long-term statistics.

## Steps

### 8.1 Tests first

`tests/test_discovery.py`:

```python
import json

from jinja2 import Environment

from discovery import discovery_messages


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
```

The second test *renders the Jinja templates* with a sample message, so a typo in a template is caught here and not in a browser.

### 8.2 `discovery.py`

Skeleton:

```python
from __future__ import annotations

DISCOVERY_PREFIX = "homeassistant"   # HA's default; must match its MQTT settings

# (key, display name, Jinja template evaluated on the meas JSON, unit, device_class)
SENSORS = [
    ("soc", "State of charge", "{{ value_json.soc_pct }}", "%", "battery"),
    ("pack_voltage", "Pack voltage", "{{ value_json.pack_voltage_v }}", "V", "voltage"),
    ("pack_current", "Pack current", "{{ value_json.pack_current_a }}", "A", "current"),
    # ... pack power, hottest cell, MOSFET temperature, lowest/highest cell voltage, cell imbalance in mV
]


def _common(device_id: str, base: str, expire_after: int) -> dict:
    """Keys shared by every entity: where the data comes from and which device owns it."""


def discovery_messages(device_id: str, base: str, n_cells: int, expire_after: int = 120):
    """Yield (topic, payload) pairs, one per Home Assistant entity."""
```

Write the missing `SENSORS` rows yourself first. The tests tell you the keys (`pack_power`, `cell_temp_max`, `imbalance`, ...) and the expected rendered values. Useful Jinja: `| max`, `| min`, `| round(0)`, `| join(', ')`, `| length`.

Then `_common`: the keys `state_topic` (`<base>/meas`), `availability_topic` (`<base>/status`) with `payload_available` `online` and `payload_not_available` `offline`, `expire_after`, and the `device` block with `identifiers`, `name`, `manufacturer`, `model`.

`discovery_messages` yields, in order:

1. one `sensor` per row of `SENSORS` (with unit, device class and `state_class: measurement`),
2. a text `sensor` for the BMS state (`{{ value_json.state }}`),
3. a text `sensor` for the active faults (`join` the list, or the word `none`),
4. a `binary_sensor` with `device_class: problem` that is ON when `errors` is not empty,
5. for each cell `k`, a sensor `cell{k+1}_v` with the template `value_json.cell_voltages_v[k]` and `entity_category: diagnostic` (hidden in the default dashboard).

Topics have the form `homeassistant/sensor/<device_id>/<key>/config`. Mind Jinja in an f-string: a literal `{{` is written `{{{{` in an f-string.

<details>
<summary>Solution: discovery.py</summary>

```python
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
```

</details>

Run `uv run pytest tests/test_discovery.py -q`. Expected: `2 passed`.

### 8.3 Publish them

In `publisher.py`, add to `_on_connect` (after `info`) a loop over `discovery_messages(self.device_id, self.base, self.model.n)` that publishes each payload with `json.dumps(...)`, **retained**, QoS 1. Add the import. Because it is in `on_connect`, a broker restart restores them.

Check with the subscriber:

```bash
docker exec -it mosq mosquitto_sub -t "homeassistant/#" -v -C 3
```

Count the retained discovery topics (expect 20 for 8 cells: 9 sensors, state, faults, the binary sensor and 8 cell voltages):

```bash
docker exec -it mosq sh -c 'mosquitto_sub -t "homeassistant/#" --retained-only -W 2 | wc -l'
```

### 8.4 See it in Home Assistant

> The Home Assistant screens below are described from its documented behaviour. They could not be run in the environment where this tutorial was written, so if the wording of a menu differs in your version, trust the screen.

Run HA in Docker (pin a version in the compose file in Part 11; for now take the current stable tag from <https://www.home-assistant.io/blog/categories/release-notes/>, shown as `YYYY.M.P`):

```bash
docker run -d --name ha -p 8123:8123 -v ha-config:/config ghcr.io/home-assistant/home-assistant:<YYYY.M.P>
```

Open <http://localhost:8123>, create the owner account, then:

1. *Settings, Devices & services, Add integration, MQTT*.
2. Broker: your computer's address as seen *from the container*. On Docker Desktop that is `host.docker.internal`, port `1883`. (In Part 11, with both in one compose file, it becomes simply `mosquitto`.)
3. Start the simulator (`dev_publish.py` from Part 7). Within seconds a device **BMS haske-bms-001** appears under the MQTT integration, with 20 entities.

Put the sensors on a dashboard: open the device, click an entity, look at the history graph after a few minutes. Increase `speed` in `dev_publish.py` to 600 and watch the sun cycle in the voltage and current graphs.

### 8.5 Prove the availability logic

1. Stop `dev_publish.py` with Ctrl+C. All entities of the device turn **Unavailable** within a second (the clean `offline`).
2. Start it again. They recover.
3. Kill it hard. They turn unavailable after about 45 s (last will) or after `expire_after` (120 s), whichever comes first.

## Your turn

1. Add a sensor `Lowest cell temperature` (`cell_temp_min`). Write its rendering test in `test_discovery.py` first, then the entry. Remember to update the count in `test_every_entity_has_a_unique_id_and_topic` (and the 20 above).
2. Change a `unique_id` and republish. What happens in HA (a duplicate, unavailable old entity)? What would you do in a real deployment to avoid that?
3. Why is `entity_category: diagnostic` right for the cell voltages and wrong for the State of Charge?

<details>
<summary>Hints</summary>

2. HA keeps the old entity (now orphaned and unavailable) and creates a new one. In a deployment, never change a `unique_id` once released. To retire an entity, publish an empty retained payload to its config topic.
3. Diagnostic entities are details for troubleshooting and are tucked away. SoC is the number users look at first.

</details>

## Checkpoint

- `uv run pytest -q` is green: `61 passed`.
- HA shows the device with 20 entities and live numbers, and handles availability as in 8.5.

## Commit

```bash
git add services/bms-simulator
git commit -m "feat(bms-simulator): Home Assistant MQTT discovery"
```

Next: [Part 9: API and scenarios](09-api-and-scenarios.md).
