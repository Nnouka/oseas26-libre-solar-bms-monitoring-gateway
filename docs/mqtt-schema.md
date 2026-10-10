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
| `bms/<id>/summary` | bridge *(planned)* | every ~15 min | 1 | yes | MPM |
| `bms/<id>/event` | bridge *(planned)* | on fault raised / cleared | 1 | no | MPM, SMS alert |
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

Not reported by the BMS (will be derived by the bridge): SoH, cycle count, imbalance, health score.
Home Assistant computes imbalance itself with a template.

## `bms/<id>/info` (implemented)

`schema`, `device_id`, `manufacturer`, `model`, `firmware`, `chemistry`, `cell_count`,
`capacity_ah`, and `limits` (the protection thresholds: cell OV/UV, charge/discharge max temp,
cell-failure voltage difference, over-current limits).

## Planned: `summary` and `event` (for MPM)

```json
// bms/<id>/summary  (retained, ~every 15 min)
{"schema":1,"device_id":"...","ts":"...","soc_pct":71.5,"soh_pct":97.2,"cycles":41.3,
 "imbalance_v":0.012,"max_temp_c":34.0,"health":"green","active_faults":[]}

// bms/<id>/event  (on change)
{"schema":1,"device_id":"...","ts":"...","type":"fault_raised","fault":"CHG_OVERTEMP",
 "severity":"critical","message":"Battery too hot to charge","action":"Check ventilation / fans"}
```

## Home Assistant discovery

Config topics `homeassistant/sensor/<id>/<key>/config` (+ one `binary_sensor` "Fault"), all pointing
at `bms/<id>/meas` with `value_template`, availability from `bms/<id>/status`, and
`expire_after: 120`. Entities: SoC, pack voltage/current/power, max cell temp, MOSFET temp,
min/max cell voltage, imbalance, BMS state, active faults, per-cell voltage.

## Security (before any field deployment)

The dev broker allows anonymous plaintext. For production: a Mosquitto `password_file`, per-device
ACLs limiting each device to `bms/<its id>/#`, and TLS on 8883.
