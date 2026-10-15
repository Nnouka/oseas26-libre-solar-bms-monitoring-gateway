# How native_sim and the BMS simulator fit together

This page answers: **what role does Libre Solar's `native_sim` play in this hackathon, and how does our own simulator mimic the real BMS?**

Read order: [native_sim-tutorial.md](native_sim-tutorial.md) (hands-on, run the firmware) → [firmware-findings.md](firmware-findings.md) (what we measured and read) → this page (how it connects to our MQTT contract) → the simulator tutorial in `services/bms-simulator/`.

## 1. Decision

- **native_sim is our ground truth for what the real firmware says.** We run it, capture its output, and make our simulator reproduce the same format, field names, units, limits and behaviour.
- **Our Python simulator is the MQTT data source** for the gateway (broker, Home Assistant, Prospect, MPM). native_sim cannot be that source by itself, see §2.
- The two are tied together by a **golden test**: for the same chip state, our simulator must produce byte-for-byte the same `#mLive` line (CRC included) that native_sim printed.

## 2. What we found out (details in firmware-findings.md)

| Question | Answer |
| --- | --- |
| Does the firmware speak MQTT or WiFi? | **No.** It speaks ThingSet over UART (and CAN on some boards). MQTT needs extra hardware or firmware, not in the repo today. |
| What does it emit without being asked? | The `#mLive` statement stream, once per second by default, a JSON `Meas` group plus CRC-32. |
| Does native_sim show realistic battery data? | **No.** The BQ76952 is emulated only enough to boot, so you see zeros and sentinels (e.g. temperature -273.1, `rCellMinVoltage_V` 10.0). That is expected. |
| Are the field names stable? | Yes: `rPackVoltage_V`, `rPackCurrent_A`, `rSOC_pct`, `rCellVoltages_V`, `rErrorFlags`, `rBmsState`, `rBalancingStatus` etc. |

Consequence for the hardware transport (README options A1/A2):

- **A1, firmware talks MQTT directly:** needs new firmware work. Out of scope unless the team has C/Zephyr capacity.
- **A2, a small gateway reads the existing serial stream and publishes MQTT:** fits the firmware as it is. We build this gateway as its own module, **`services/wifi-sim`** (tutorial Part 10). It reads `#mLive` lines from the real `native_sim` (pseudo-terminal or TCP bridge) or from our simulator's `/live` endpoint, checks the CRC, maps to the team schema and publishes over the network. It also simulates the WiFi link going down and up.
  Limit: `native_sim` has no cells, so its stream is valid but is not published (the schema needs at least one cell voltage). Realistic data from the real firmware needs the emulated chip fed from the battery model (stretch goal).

## Production-parity rule

Only build what the production system can use without major changes:

- The **relay (`services/wifi-sim`) is the only device-side MQTT publisher** and only forwards: `meas`, `info`, `status`. The BMS (the firmware rig, or a real C1) only produces the serial `#mLive` line.
- **Health is derived server-side**: `summary` and `event` come from `services/bms-bridge` using the shared package `packages/bms-health`. A real relay (ESP32, Cicada) cannot carry that logic, and the bridge behaves the same whichever transport brought the data.
- The source is one setting on the gateway: `SOURCE=http://...` (simulator), `file:`/`tcp://` (native_sim), `serial:COM5` (real C1). Consumers (Home Assistant, MPM, Prospect) cannot tell which.
- Every field of the MQTT `meas` message comes from a ThingSet `Meas` item. Anything else (SoH, cycles, imbalance) is derived downstream, never invented by the simulator.
- Open questions for the case giver (how MPM plugins ingest data, the Cicada UART contract) are listed in tutorial Part 12.

## 3. Two layers in the simulator

```text
battery.py   physics: OCV, internal resistance, temperature, current profile
firmware.py  what the firmware does with it: SoC estimate, limits, error flags, state, balancing
thingset.py  encode/decode the #mLive line + CRC-32
mapping.py   ThingSet Meas  ->  team MQTT JSON (docs/mqtt-schema.md)
publisher.py MQTT only
```

Keeping physics apart from firmware logic is the point: the real BMS reports what the **firmware believes** (an SoC estimate that can drift, limits with hysteresis), not the chemistry's true state.

## 4. Firmware behaviour to mimic

Use the real LFP defaults, not guesses:

- Cell OV 3.80 V (reset 3.40), UV 2.50 V (reset 3.10).
- Charge temp limits 0 to 45 °C, discharge -20 to 45 °C, hysteresis 5 °C.
- Over-current limit equals 1C of capacity, short circuit 2× that.
- Balancing only after 1800 s idle (current below 0.1 A), cell diff over 10 mV, cell above 3.30 V.
- SoC initialised from the OCV table at boot, then coulomb counted.
- 16 entries in `rCellVoltages_V`; unused channels read 0.
- Error flag bit positions as listed in firmware-findings.md.

## 5. Mapping to the team MQTT contract

The contract is the one on branch `feat/mqtt-simulator-ha`, `docs/mqtt-schema.md` (topics `bms/<id>/status|info|meas`). Do not invent a second schema. Mapping:

| MQTT field | ThingSet item | Transform |
| --- | --- | --- |
| `pack_voltage_v` | `rPackVoltage_V` | as is |
| `pack_current_a` | `rPackCurrent_A` | as is, positive = charging (verify sign on hardware) |
| `soc_pct` | `rSOC_pct` | as is |
| `cell_voltages_v` | `rCellVoltages_V` | drop unused trailing channels |
| `cell_temps_c` | `rCellTemps_degC` | as is |
| `mosfet_temp_c` | `rMOSFETTemp_degC` | as is |
| `balancing_mask` | `rBalancingStatus` | as is |
| `errors` | `rErrorFlags` | bits to names |
| `state` | `rBmsState` | 0 OFF, 1 CHG, 2 DIS, 3 NORMAL, 4 SHUTDOWN to names |
| `device_id`, `ts`, `schema` | none | added by the gateway/simulator |

Open item for the team: the schema has no field for the sentinel/"no data" case (temperature -273.1). Decide whether to publish `null`.

## 6. Definition of done for the simulator milestone

- Starts a `normal` scenario with no hardware and no manual steps.
- The `#mLive` line it produces matches native_sim byte for byte for the empty-chip state (golden test).
- Publishes the team schema to Mosquitto, with retained `status` and LWT.
- `cell_drift` and `overheat` scenarios raise firmware-style error flags.
- Broker restart neither crashes the service nor resets the model.
- `/health` is accurate; `docker compose up` brings the stack up.
- Docs say which values are modelled and which come from firmware.

## 7. Stretch goals

1. Serve the simulated `#mLive` stream on a TCP port (the gateway already reads `/live` over HTTP and TCP).
2. Add a ThingSet request/response endpoint (`?Meas`, `=Conf`) for read-only queries.
3. ~~Extend native_sim with a richer BQ76952 mock~~ Done without touching the firmware: the **firmware rig** (`services/firmware-rig`) writes the battery into the emulated chip through the firmware's own shell (`i2c write`), models the chip's protection trips in Python, and reads the firmware's switch state back from `rBmsState`. Never describe a mock as validation of real protection hardware.

## References

- [Libre Solar firmware simulator docs](https://libre.solar/bms-firmware/src/dev/simulator.html)
- [Workspace setup](https://libre.solar/bms-firmware/src/dev/workspace_setup.html)
- [BMS C1 manual](https://libre.solar/bms-c1/manual/)
- [ThingSet protocol](https://thingset.io/)
- [Project README](../README.md)
