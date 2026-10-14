# MQTT topic + JSON schema (v1, DRAFT for review)

Source of truth for every consumer (Home Assistant, MPM ingestor, Prospect).
Field names mirror the ThingSet `Meas` group of
[LibreSolar/bms-firmware](https://github.com/LibreSolar/bms-firmware) (`app/src/data_objects.c`),
with the unit as a suffix (`_v`, `_a`, `_c`, `_pct`). SI units, no rounding surprises.

**Conventions:** `schema` = integer version · `ts` = ISO 8601 UTC (`2026-10-09T14:32:05Z`) ·
pack current **> 0 = charging**, **< 0 = discharging** · `<id>` = device id, e.g. `haske-bms-001`.

## Topics

| Topic | Publisher | Rate | QoS | Retain | Consumers |
|---|---|---|---|---|---|
| `bms/<id>/status` | device (+ Last Will) | on connect / unclean disconnect | 1 | yes | all ("last seen", availability) |
| `bms/<id>/info` | device | on every (re)connect | 1 | yes | all (static metadata + limits) |
| `bms/<id>/meas` | device / simulator | every `PUBLISH_INTERVAL_S` (default 5 s) | 0 | no | Home Assistant, bridge, Prospect |
| `bms/<id>/summary` | gateway (wifi-sim) or bridge | every ~15 min | 1 | yes | MPM |
| `bms/<id>/event` | gateway (wifi-sim) or bridge | on fault raised / cleared | 1 | no | MPM, SMS alert |
| `homeassistant/<component>/<id>/<key>/config` | simulator now, bridge later | on every (re)connect | 1 | yes | Home Assistant |

`status` payload is the plain string `online` or `offline`. The broker publishes `offline` via the
Last Will if the device disappears without a clean disconnect.

## `bms/<id>/meas` (implemented)

```json
{
  "schema": 1,
  "device_id": "haske-bms-001",
  "ts": "2026-10-09T14:32:05Z",
  "state": "NORMAL",
  "pack_voltage_v": 26.41,
  "pack_current_a": -12.3,
  "soc_pct": 71.5,
  "cell_voltages_v": [3.301, 3.298, 3.305, 3.299, 3.302, 3.296, 3.300, 3.303],
  "cell_temps_c": [28.4, 27.6],
  "mosfet_temp_c": 34.0,
  "balancing_mask": 0,
  "errors": []
}
```

| Field | Type | ThingSet origin | Notes |
|---|---|---|---|
| `state` | `OFF` \| `CHG` \| `DIS` \| `NORMAL` \| `SHUTDOWN` | `rBmsState` | `CHG` = only charging allowed, `DIS` = only discharging allowed |
| `pack_voltage_v` | number | `rPackVoltage_V` | |
| `pack_current_a` | number | `rPackCurrent_A` | + charging / - discharging |
| `soc_pct` | number 0..100 | `rSOC_pct` | |
| `cell_voltages_v` | number[] | `rCellVoltages_V` | one entry per cell |
| `cell_temps_c` | number[] | `rCellTemps_degC` | |
| `mosfet_temp_c` | number | `rMOSFETTemp_degC` | |
| `balancing_mask` | int | `rBalancingStatus` | bit *n* = cell *n* is balancing |
| `errors` | string[] | `rErrorFlags` | names, not a bitmask; see below |

**Error names** (bits of `BMS_ERR_*`): `CELL_UNDERVOLTAGE`, `CELL_OVERVOLTAGE`, `SHORT_CIRCUIT`,
`DIS_OVERCURRENT`, `CHG_OVERCURRENT`, `OPEN_WIRE`, `DIS_UNDERTEMP`, `DIS_OVERTEMP`,
`CHG_UNDERTEMP`, `CHG_OVERTEMP`, `INT_OVERTEMP`, `CELL_FAILURE`, `DIS_OFF`, `CHG_OFF`, `FET_OVERTEMP`.

Not reported by the BMS (derived by the bridge, see `summary`): SoH, cycle count, imbalance, health score.
Home Assistant computes imbalance itself with a template.

## `bms/<id>/info` (implemented)

`schema`, `device_id`, `manufacturer`, `model`, `firmware`, `chemistry`, `cell_count`,
`capacity_ah`, and `limits` (the protection thresholds: cell OV/UV, charge/discharge max temp,
cell-failure voltage difference, over-current limits).

## `bms/<id>/summary` (implemented, published by the WiFi gateway, or by the bridge for a real MQTT device)

Low-frequency, decision-relevant slice for MPM. Retained, QoS 1. Published every
`SUMMARY_INTERVAL_S` (default 900 s = 15 min) **and immediately** when the fault set or the health colour changes.

```json
{
  "schema": 1, "device_id": "haske-bms-001",
  "ts": "2026-10-09T14:32:05Z", "last_seen": "2026-10-09T14:32:05Z",
  "state": "NORMAL", "soc_pct": 71.5, "pack_voltage_v": 26.41,
  "soh_pct": 99.9, "soh_method": "cycle_model", "cycles": 1.2,
  "imbalance_v": 0.012, "max_temp_c": 34.0,
  "health": "green", "health_score": 100, "health_reasons": [],
  "active_faults": []
}
```

| Field | Derived how |
|---|---|
| `cycles` | integral of \|pack current\| over time / (2 x capacity). Capacity comes from `info`. |
| `soh_pct` | **Estimate**, not measured: 100 % minus 20 % per 3000 equivalent cycles (`soh_method: "cycle_model"`). Replace with a measured value if the firmware ever provides one. |
| `imbalance_v` | max minus min of `cell_voltages_v` |
| `health` / `health_score` / `health_reasons` | green/amber/red from active faults (critical = red, warning = amber), temperature (>=40 C amber, >=45 C red), imbalance (>=100 mV amber, >=200 mV red; **only judged at 10-90 % SoC** because the LFP curve is steep at the ends), SoC < 15 % (amber), SoH < 90 % amber / < 80 % red. Score = 100 minus penalties. |

## `bms/<id>/event` (implemented, published by the WiFi gateway, or by the bridge for a real MQTT device)

On change only. QoS 1, not retained.

```json
{"schema":1,"device_id":"haske-bms-001","ts":"2026-10-09T14:32:05Z",
 "type":"fault_raised","fault":"CHG_OVERTEMP","severity":"critical",
 "message":"Battery too hot to charge",
 "action":"Check ventilation and fans; shade the enclosure; charging resumes when it cools."}
```

`type`: `fault_raised` | `fault_cleared` | `device_offline` | `device_online`.
`severity`: `critical` | `warning` | `info`. `fault` is one of the error names above (null for device events).
`message` and `action` are plain language for operators and SMS alerts.

## Home Assistant discovery

Config topics `homeassistant/sensor/<id>/<key>/config` (+ one `binary_sensor` "Fault"), all pointing
at `bms/<id>/meas` with `value_template`, availability from `bms/<id>/status`, and
`expire_after: 120`. The bridge adds five more entities on the same device from `summary` (health, health score, health reasons, estimated SoH, cycles). Simulator entities: SoC, pack voltage/current/power, max cell temp, MOSFET temp,
min/max cell voltage, imbalance, BMS state, active faults, per-cell voltage.

## Security (before any field deployment)

The dev broker allows anonymous plaintext. For production: a Mosquitto `password_file`, per-device
ACLs limiting each device to `bms/<its id>/#`, and TLS on 8883.
