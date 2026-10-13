# What the Libre Solar BMS firmware actually does

Findings from reading the firmware source and running it in Zephyr `native_sim`. The goal is to
know what the real BMS will send and how it behaves, so that this repository's simulator can mimic
it instead of guessing.

**Evidence base.** Everything below was read from source or observed by running the firmware on
2026-10-13. Statements marked *(source)* come from reading code, *(observed)* from running
`native_sim`, and *(to verify)* are not yet confirmed and need the real board.

| Component | Revision used |
| --- | --- |
| `LibreSolar/bms-firmware` | `779d201` (2026-08-18, `main`) |
| Zephyr | 4.4.2 (`v4.4-branch`) |
| ThingSet SDK (`thingset-zephyr-sdk`) | `8b32f7d` |
| Host | Ubuntu 24.04 x86-64, `native_sim` (32-bit), host GCC 13.3 |

## 1. The firmware in one picture

```text
 cells ── BQ76952 chip ──I2C── driver ── BMS core ──── ThingSet data objects ──┬── serial (UART)
          (measures, protects)  bq769x2   state machine      Meas / Conf / Input   ├── CAN bus
                                          SoC estimate       "mLive" subset        └── Bluetooth LE
```

- The **BQ76952 chip** measures cell voltages, current and temperatures, and enforces the
  protection thresholds the firmware writes into it. The firmware reads its results and error
  flags *(source: `drivers/bms_ic/bq769x2`)*.
- The **BMS core** (`app/src/bms_common.c`, `bms_soc.c`) runs the state machine and the
  State-of-Charge estimate.
- **ThingSet** (`app/src/data_objects.c`) exposes everything as named data objects, grouped into
  `Device`, `Conf`, `Meas` and `Input`.
- **Transports:** ThingSet over serial, CAN and Bluetooth *(source: `app/boards/bms_c1.conf`)*.

### There is no MQTT and no WiFi in this firmware

No MQTT client or WiFi configuration appears in the firmware or its board configuration. The
ThingSet SDK documentation states that "interfaces like MQTT and WebSocket over WiFi are currently
under development" *(source: `thingset-zephyr-sdk/docs/index.rst`)*.

Consequence for the hackathon's two transport options:

| Path | What it needs |
| --- | --- |
| **A1**: BMS publishes MQTT over WiFi | New firmware work: WiFi, an MQTT client and a mapping from ThingSet to MQTT. Not available out of the box. |
| **A2**: BMS → UART → Cicada → cellular → broker | Fits what the firmware already does: it streams ThingSet lines on its UART (see section 2). The Cicada firmware has to parse them and forward them. |

## 2. What it sends: the `#mLive` stream

Live reporting is enabled by default and repeats every 1000 ms
(`CONFIG_THINGSET_REPORTING_LIVE_ENABLE_PRESET=y`, `..._PERIOD_PRESET_MS=1000`)
*(source: `thingset-zephyr-sdk/Kconfig`)*. On the serial interface each report is one text line,
a ThingSet **statement**:

```text
#mLive {"Meas":{"rPackVoltage_V":0.00,"rStackVoltage_V":0.00,"rPackCurrent_A":0.00,"rCellTemps_degC":[-273.1,0.0],"rICTemp_degC":-273.1,"rMOSFETTemp_degC":-273.1,"rSOC_pct":0.0,"rErrorFlags":0,"rBmsState":3,"rCellVoltages_V":[0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000],"rCellAvgVoltage_V":null,"rCellMinVoltage_V":10.000,"rCellMaxVoltage_V":0.000,"rBalancingStatus":0}} 437123CA#
```

*(observed, `native_sim`, one line per second)*

Anatomy of a line:

| Part | Meaning |
| --- | --- |
| `#` | A statement (unsolicited report), as opposed to `?` request or `:` response |
| `mLive` | The subset being reported: the 14 `Meas` items listed by `?` |
| `{...}` | JSON payload. Numbers use fixed decimals per item. `NaN` becomes `null`. |
| ` 437123CA` | CRC-32 as 8 upper-case hex digits |
| `#` | End marker |

**CRC rule (observed and verified):** the CRC-32 is computed over the text from the leading `#` up
to, but not including, the space before the CRC. In Python,
`zlib.crc32(line_up_to_the_space.encode())` reproduces `437123CA` for the line above.

On the Libre Solar BMS C1 the ThingSet serial port is `uart0` at **115200 baud**
*(source: `boards/libresolar/bms_c1/bms_c1.dts`)*. On `native_sim` it is `uart_1`, exposed as a
pseudo-terminal whose path is printed at startup (`uart_1 connected to pseudotty: /dev/pts/N`).

## 3. The measurement items and how they map to our MQTT message

Items of the `Meas` group *(source: `app/src/data_objects.c`)*. "Decimals" is how many digits
the firmware prints.

| ThingSet item | Type / decimals | Meaning | MQTT `meas` field (see `docs/mqtt-schema.md`) |
| --- | --- | --- | --- |
| `rPackVoltage_V` | float, 2 | Sum of cell voltages as measured by the chip ("stack voltage" in the driver) | `pack_voltage_v` |
| `rStackVoltage_V` | float, 2 | Voltage measured on the pack terminals; only if the IC has switches | not mapped (names are easy to confuse with the line above) |
| `rPackCurrent_A` | float, 2 | **Positive = charging**, negative = discharging | `pack_current_a` |
| `rCellTemps_degC` | float array, 1 | Cell thermistors (2 on the C1) | `cell_temps_c` |
| `rICTemp_degC` | float, 1 | BMS chip temperature | not mapped |
| `rMOSFETTemp_degC` | float, 1 | MOSFET temperature | `mosfet_temp_c` |
| `rSOC_pct` | float, 1 | State of charge estimated by the firmware, 0 to 100 | `soc_pct` |
| `rErrorFlags` | uint32 | Bit mask `BMS_ERR_*` (table in section 5) | `errors` (decoded to names) |
| `rBmsState` | uint8 | 0 OFF, 1 CHG, 2 DIS, 3 NORMAL, 4 SHUTDOWN | `state` |
| `rCellVoltages_V` | float array, 3 | **16 entries** on the C1 (`BMS_IC_MAX_CELLS`); unused entries are about 0 V | `cell_voltages_v` (keep entries above 0.5 V) |
| `rCellAvgVoltage_V` | float, 3 | Average of connected cells; `null` when none | not mapped (derivable) |
| `rCellMinVoltage_V` | float, 3 | Lowest connected cell; **10.000 when none** | not mapped |
| `rCellMaxVoltage_V` | float, 3 | Highest cell | not mapped |
| `rBalancingStatus` | uint32 | Bit n = cell channel n is balancing | `balancing_mask` |

Things to know when converting:

- **No clock.** The firmware sends no timestamp. `ts` has to be added by whoever receives the line.
- **No device id.** The ThingSet node id (`pNodeID`, e.g. `9C232EA9CABFB9A2`) is generated per
  device; it is readable with `?` but is not part of the `#mLive` line. `device_id` has to come
  from configuration.
- **The cell count is not sent.** Count the entries above 0.5 V (the driver itself uses 0.5 V as
  its "connected" test). *(to verify on a real pack: which of the 16 channels carry an 8-cell
  pack.)*
- **No SoH, cycle count, imbalance or health score.** They are not in the firmware, so the gateway
  derives them (imbalance = max minus min cell voltage).
- **Absent sensors read -273.1.** On `native_sim` the emulated chip reports 0 K for temperatures.

## 4. Default configuration (`?Conf`)

Defaults come from `bms_init_config()` in `app/src/bms_common.c`, selected at build time by
`CONFIG_CELL_TYPE` (default LFP) and `CONFIG_BAT_CAPACITY_AH` (default 50). Values below are from
`?Conf` on `native_sim` *(observed)* and match the source. Note the ThingSet values for delays show
small rounding (`1999` instead of `2000`, `195` instead of `200`) because the chip stores them in
coarse steps.

| Item | LFP default | Notes |
| --- | --- | --- |
| `sNominalCapacity_Ah` | 50 | build option; also changeable at runtime |
| `sCellOvervoltage_V` / reset | 3.80 / 3.40 | trips at 3.80 V, clears below 3.40 V |
| `sCellUndervoltage_V` / reset | 2.50 / 3.10 | trips at 2.50 V, clears above 3.10 V |
| `sCellOvervoltageDelay_ms`, `sCellUndervoltageDelay_ms` | 2000 | fault must persist this long |
| `sChgMaxTemp_degC`, `sDisMaxTemp_degC` | 45, 45 | over-temperature limits |
| `sChgMinTemp_degC`, `sDisMinTemp_degC` | 0, -20 | under-temperature limits |
| `sTempLimitHysteresis_degC` | 5 | clears 5 degC inside the limit |
| `sChgOvercurrent_A`, `sDisOvercurrent_A` | = capacity (1C) | observed 52 A for a 50 Ah default: the chip quantises the value |
| `sShortCircuitLimit_A` | 2 x the discharge over-current limit | observed 80 A, again after the chip's quantisation |
| `sChgOvercurrentDelay_ms`, `sDisOvercurrentDelay_ms` | 320 | |
| `sBalTargetVoltageDiff_V` | 0.010 | balance cells more than 10 mV above the lowest |
| `sBalMinVoltage_V` | 3.30 | only balance above this cell voltage |
| `sBalIdleDelay_s` | 1800 | pack must have been idle 30 minutes |
| `sBalIdleCurrent_A` | 0.1 | "idle" means below 0.1 A |
| `sOcvPoints_V` / `sSocPoints_pct` | 21 points, 5 % spacing | see below |

Internal limits that are **not** in ThingSet but exist in `struct bms_ic_conf`:
`cell_chg_voltage_limit` = 3.55 V and `cell_dis_voltage_limit` = 2.80 V for LFP. They are used for
a simplified SoC estimate only when no OCV table is present, and in this firmware revision nothing
sets the `bms->full` / `bms->empty` flags that would use them *(source: grep shows they are only
read)*.

LFP open-circuit-voltage table the firmware uses for SoC (100 % down to 0 %, 5 % steps):

```text
3.392 3.314 3.309 3.308 3.304 3.296 3.283 3.275 3.271 3.268 3.265
3.264 3.262 3.252 3.240 3.226 3.213 3.190 3.177 3.132 2.833
```

A fully charged LFP cell at rest is 3.39 V in this table, not 3.6 V.

## 5. Behaviour

### State machine (`bms_state_machine`)

| State (`rBmsState`) | Switches | Leaves when |
| --- | --- | --- |
| OFF (0) | both open | discharge allowed → DIS; else charge allowed → CHG |
| CHG (1) | charge on | charge not allowed → OFF; discharge allowed → NORMAL |
| DIS (2) | discharge on | discharge not allowed → OFF; charge allowed → NORMAL |
| NORMAL (3) | both on | discharge not allowed → CHG; charge not allowed → DIS |
| SHUTDOWN (4) | both open | shutdown button held 3 s |

Charging is allowed when no charge-type error flag is set and `wChgEnable` is true; discharging
likewise with the discharge error flags and `wDisEnable` *(source)*. The firmware polls the chip
in a loop (`BMS_IC_POLLING_INTERVAL_MS`) and runs the state machine on every pass.

Observed: after boot the log prints `OFF -> DIS` then `DIS -> NORMAL`. Writing
`wChgEnable=false` gives `NORMAL -> DIS` and `rBmsState` becomes 2.

### Error flags (`rErrorFlags`, bit n = 2^n)

| Bit | Name | Blocks |
| --- | --- | --- |
| 0 | `CELL_UNDERVOLTAGE` | discharge |
| 1 | `CELL_OVERVOLTAGE` | charge |
| 2 | `SHORT_CIRCUIT` | discharge |
| 3 | `DIS_OVERCURRENT` | discharge |
| 4 | `CHG_OVERCURRENT` | charge |
| 5 | `OPEN_WIRE` | both |
| 6 | `DIS_UNDERTEMP` | discharge |
| 7 | `DIS_OVERTEMP` | discharge |
| 8 | `CHG_UNDERTEMP` | charge |
| 9 | `CHG_OVERTEMP` | charge |
| 10 | `INT_OVERTEMP` | both |
| 11 | `CELL_FAILURE` | both |
| 12 | `DIS_OFF` | (informational: discharge switch off when it should be on) |
| 13 | `CHG_OFF` | (informational) |
| 14 | `FET_OVERTEMP` | not used in the allow/deny test |

### State of charge

- At boot: SoC is set from the average cell voltage using the OCV table (`bms_soc_reset(-1)`).
- Afterwards: coulomb counting. Charge in mA·s is accumulated and applied as
  `delta% = mAs / (capacity_Ah × 3.6e4)`, only once the change exceeds 0.1 %. SoC is clamped to
  0 to 100.
- There is no correction at the full or empty end in this revision. The firmware's SoC therefore
  **drifts away from the true charge** over time. A simulator that reports the true average cell
  charge is not mimicking the firmware.

### Balancing

Automatic balancing is on by default and runs inside the chip with the settings in section 4:
idle for 30 minutes (below 0.1 A), cell above 3.30 V, and cells more than 10 mV above the lowest
get balanced. *(to verify: the bleed current of the chip's internal balancing.)*

## 6. What `native_sim` does, and does not do

`native_sim` compiles the **real application** (state machine, SoC code, ThingSet, configuration
storage) as a Linux program. The BQ76952 is replaced by an I2C **emulator**
(`bq769x2_emul.c`) that is only a block of memory: it returns whatever was written into it and
does not model cells, current or temperature. Unit tests write register values into it from C.

Observed consequences:

| What | Result |
| --- | --- |
| Boot | `Detected 0 cells`, `Activated BMS IC 0x0`, then `OFF -> DIS -> NORMAL` with error flags 0 |
| `?Meas` | all voltages and current 0, temperatures -273.1, SoC 0, `rCellAvgVoltage_V` is `null`, `rCellMinVoltage_V` is 10.000, `rBmsState` 3 |
| First `#mLive` line at boot | slightly different from later ones: temperatures `0.0`, `rBmsState` 0, average and min voltage `0.000`, before the first chip read |
| `#mLive` | one line per second on the `uart_1` pseudo-terminal |
| `?Conf` | the default configuration of section 4 |
| Writing configuration (`=Conf {...}`) | works and is saved to the emulated EEPROM; shortly afterwards the log showed `Failed to read data from BMS IC: -5` (`Failed to read data at 0x0083 after 100 attempts`), so the emulator apparently cannot service all of the chip's reads after a reconfiguration |
| Writing inputs (`=Input {...}`) | works and drives the state machine |
| Bluetooth | `Bluetooth init failed (err -19)` unless a Bluetooth adapter or socket is supplied |
| CAN | `Cannot open vcan0` unless a virtual CAN interface exists (`ip link add dev vcan0 type vcan`) |

So `native_sim` is a reliable reference for **message format, field names, units, defaults and
state-machine behaviour**. It is not a source of realistic battery data.

## 7. Documentation problems found while building it

These break the instructions in Libre Solar's simulator page and in this repository's earlier
`native_sim-tutorial.md`:

1. **Build from the `app` folder.** `west build` in the repository root fails with
   `Unknown CMake command "zephyr_include_directories"` because the root `CMakeLists.txt` is a
   Zephyr module, not the application. The firmware's own CI runs `cd app` first.
2. **Install 32-bit build support.** `native_sim` defaults to a 32-bit executable, which needs
   `gcc-multilib` and `g++-multilib`.
3. **Select the host toolchain** (`export ZEPHYR_TOOLCHAIN_VARIANT=host`) unless the Zephyr SDK is
   installed.
4. **The 64-bit variant ignores the app's native_sim files.** `-b native_sim/native/64` does not
   pick up `app/boards/native_sim.conf` and `native_sim.overlay` automatically, and the build then
   fails in `bms_soc.c`. Passing them explicitly
   (`-- -DEXTRA_CONF_FILE=boards/native_sim.conf -DEXTRA_DTC_OVERLAY_FILE=boards/native_sim.overlay`)
   is the workaround.
5. **Quote JSON for the Zephyr shell.** Inside `select thingset` the shell consumes plain double
   quotes: `=Input {"wChgEnable":false}` returns `:A0` (bad request). Write
   `=Input {\"wChgEnable\":false}`.

## 8. What the simulator must mimic

| Requirement | Why | Section |
| --- | --- | --- |
| Emit the `#mLive {...} CRC#` line, with the same item order, decimals, `null` and CRC | the Cicada relay and any ThingSet tool will parse exactly this | 2, 3 |
| Report **16** cell-voltage entries and 2 temperatures | what the C1 reports; the gateway must cope with the zero entries | 3 |
| Use the firmware's LFP OCV table and the default limits | realistic voltages and fault thresholds | 4 |
| Report an **estimated** SoC that is coulomb-counted from boot and can drift | what the firmware does | 5 |
| State machine, error flag bits, hysteresis and delays as in the firmware | consumers key off `state` and `errors` | 5 |
| Balance only after 30 idle minutes above 3.30 V | what the configuration says | 4, 5 |
| No SoH, cycles, imbalance, health or timestamps in the firmware-level data | these are derived downstream | 3 |

A strong test follows from this: for an "empty chip" input the simulator's encoder must produce
the same line, byte for byte and CRC included, as `native_sim` (see section 2).

## 9. Open questions for the real hardware

- Which cell channels carry an 8-cell pack, and what do unused channels read?
- Balancing current and exact start conditions of the chip's internal balancing.
- Real timing of `#mLive` on the C1 (the 1 s default can be changed in the build).
- Whether the hackathon boards run this revision or a tagged release, and what
  `cFirmwareVersion` reports (it is empty in the `native_sim` build).
- Whether `rPackCurrent_A` and `rMOSFETTemp_degC` are present on the board variant in use (they
  depend on `CONFIG_BMS_IC_SWITCHES` and current monitoring).
