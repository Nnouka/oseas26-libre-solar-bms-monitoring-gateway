# Firmware rig, dumb relay and server-side health: design

Status: draft for review · Date: 2026-10-14 · Path: README transport option A2 (BMS → UART → relay → broker)

## 1. Goal

A test setup whose output is indistinguishable from a deployed Libre Solar BMS, so that everything downstream
of the serial line is the code that will run in the field.

Success looks like this: the same relay, bridge, ingestor, MPM plugin and Home Assistant setup work unchanged
whether the `#mLive` line comes from the **rig** (real firmware, simulated battery) or from a **real BMS C1**
on a serial port. Only the relay's `SOURCE` setting differs.

## 2. Background (what we learned)

- The firmware speaks ThingSet over UART and has no MQTT or WiFi (`docs/firmware-findings.md`). Path A1
  (firmware publishes MQTT) is new Zephyr work and is out of scope here.
- `native_sim` runs the real application. The BQ76952 chip is an I2C emulator that is only memory
  (`bq769x2_emul.c`).
- **Spike result (2026-10-14).** With `CONFIG_I2C_SHELL=y` the firmware's own shell accepts
  `i2c write i2c@100 0x8 <reg> <bytes>`. The write reaches the emulator memory and `i2c read` returns it.
  Cell voltages and current written after boot appeared in the real `#mLive` stream and in `thingset ?Meas`.
  No C code and no firmware patch were needed. `native_sim` also supports `-rt-ratio=<n>` and `-no-rt`.
- Today's Python `firmware.py` is a re-telling of the real firmware. The rig replaces it with the real thing.

## 3. Decisions

| # | Decision | Reason |
|---|----------|--------|
| D1 | The deployed topology is A2. The relay is a **dumb pipe**: `#mLive` in, `meas` out, plus `info`, `status`/Last Will and offline handling. | A real relay (ESP32 or Cicada, C) cannot carry a health model, and A1 has no relay at all. |
| D2 | Health, cycle counting and fault events (`summary`, `event`) are derived **server-side**, once, by `bms-bridge`. | Same behaviour for A1 and A2, one place to change, central updates. |
| D3 | The health logic becomes **one shared Python package** (`bms-health`) used by the bridge. The copies in `bms-simulator/health.py` (removed) and `wifi-sim/health.py` (to be removed) go away. | Three copies already existed. The user asked for a separate package. |
| D4 | The simulator's scenario concept stays inside the simulator/rig (physics control only). It never reaches MQTT or MPM. | A real BMS has no scenario; keeping it out keeps production parity. |
| D5 | The rig injects the battery into the firmware **through the firmware's own shell** (`i2c write`), not through a C shim. | Proven by the spike; nothing to maintain in Zephyr. |

## 4. Architecture

```text
 rig container                                                        site (identical)
┌───────────────────────────────────────────────┐
│ battery.py + chemistry.py + chip.py (model)   │
│        │ ChipReading                          │
│  encoder: ChipReading -> BQ76952 registers    │
│        │ i2c write (shell on uart_0)          │
│        v                                      │
│  native_sim firmware (real) ──uart_1──> TCP :  │ ─#mLive─> relay ─meas─> broker ─> bridge ─summary/event─> ingestor ─> MPM
│        ^                                      │            (SOURCE=      |                                     \-> Home Assistant
│  read back: FET status, balancing (closed loop)│             tcp://rig:…   \-> Prospect
└───────────────────────────────────────────────┘            or serial:COM5 on a real C1)
```

## 5. Components

### 5.1 Rig (`services/firmware-rig`, new)
Purpose: be a BMS C1 for software that cannot tell the difference.

- **Image:** builds the `bms-firmware` `native_sim` binary with `app/boards/native_sim.conf` plus an overlay
  `rig.conf` (`CONFIG_I2C_SHELL=y`, `CONFIG_SHELL_CMDS_SELECT=n`, possibly a larger `CONFIG_I2C_SHELL_BUFFER_SIZE`).
  Build uses `west` with `ZEPHYR_TOOLCHAIN_VARIANT=host` as in `docs/native_sim-tutorial.md`. The binary
  is 32-bit Linux, so the image is Linux-only (the user already builds it in WSL).
- **Process model:** the driver starts the binary with `-rt-ratio=<SPEED>` and talks to its shell over a pipe
  (stdin/stdout = `uart_0`). It reads the `#mLive` stream from the `uart_1` pseudo-terminal and re-serves
  those lines on a TCP port (what `socat` does in the tutorial, done in-process for portability).
- **Driver loop**, once per simulated step:
  1. step the model (`battery.py`) → `ChipReading`
  2. encode to register writes (5.2) and send them with `i2c write`
  3. read back what the firmware commanded (FET status, balancing) with `i2c read` → feed `chg_on`,
     `dis_on`, `bleed_mask` into the next model step (closed loop, as `firmware.py` did)
- **Ports:** TCP for `#mLive` (default 3333), HTTP for `/health`, `/state`, `POST /scenario/<name>` (physics only).
- **Replaces:** `firmware.py`, `thingset.py` encoding and the `/live` HTTP endpoint of `bms-simulator`.
  `chemistry.py`, `chip.py` and `battery.py` move into (or are imported by) the rig. `bms-simulator`
  becomes the pure-Python fast fallback for people without the 32-bit toolchain, or is retired once the
  rig is proven. Decision deferred to the plan.

### 5.2 Register encoder (inside the rig, pure functions)
Maps `ChipReading` to byte strings for the direct-command registers the driver reads
(`bq769x2_registers.h`): cell voltages at `0x14 + 2·i` (mV, int16 LE), stack voltage `0x34`,
current `0x3A`, temperatures `0x68` (internal) and `0x70`/`0x72` (thermistors), plus the FET and
balancing status registers for read-back. Contiguous registers are written in one command.
Pure and unit-tested against the values the driver code expects (golden values from `bq769x2.c`).

### 5.3 Relay (`services/wifi-sim`, trimmed)
Keeps: source adapters (`serial:`, `tcp://`, `file:`, `http://`), CRC check, `mapping.py`, `Meas`
validation, `info`/`status`/Last Will, WiFi up/down, discovery, rate limiting.
Removes: the health tracker and the `summary`/`event` publishing added on 2026-10-14. It is the reference
specification for the real ESP32/Cicada relay and is named accordingly in docs (the "WiFi" in the name is
only one possible link).

### 5.4 `bms-health` package (new, shared)
Extracted from `bms-bridge/logic.py` (the original). Contents: fault catalog, `assess_health`,
`DeviceTracker`. No MQTT, no I/O. Consumed by `bms-bridge` as a path dependency. Package layout and
publishing method (path dependency vs. wheel) is a plan-level choice; the interface is
`on_meas(meas) -> (events, summary_now)` and `summary() -> dict`, unchanged.

### 5.5 Bridge, ingestor, MPM plugin
Unchanged in behaviour. `bms-bridge` returns to the compose stack. `mpm-ingestor` keeps `validate.py` and
the `received` counters. Scenario fields do not exist anywhere in these components.

## 6. Data flow and timing

- The firmware emits `#mLive` once per second of **firmware time**. With `-rt-ratio=R` that is R lines per
  real second. The model steps at the same simulated rate, so `SPEED = R` and the bridge's `TIME_SCALE = R`.
  In the field both are 1.
- Relay `meas` publishing is rate limited by real time as today; cycle counting in the bridge uses
  `ts` deltas × `TIME_SCALE`, as in `bms-bridge`.
- The relay stamps `ts` from its own clock (the firmware stream has none). On a real deployment the relay
  needs a clock source (NTP); documented as a field requirement, not solved here.

## 7. Error handling

- Shell write fails or the binary exits: the driver restarts the process, re-applies the last register
  image and logs it. The relay already reconnects to the TCP source.
- Firmware reports `Failed to read data from BMS IC` (seen after `=Conf`): the driver exposes a counter on
  `/health`; the rig does not hide it.
- `CRC` failures and `NoCells` are handled by the relay as today.

## 8. Testing

- **Encoder:** pure unit tests (register values for known readings, contiguous-write batching, signed values).
- **Golden test with the real firmware:** in the rig image, write a fixed `ChipReading`, capture the real
  `#mLive`, assert `rCellVoltages_V`, `rPackCurrent_A` and error flags. This replaces the `firmware.py`
  golden test and is the proof that the rig behaves like the firmware.
- **Closed loop:** drive `overheat` and assert the firmware raises `CHG_OVERTEMP` and the model sees the FET
  turn off (and recover).
- **Parity test:** the same recorded `#mLive` fixture fed through `file:`, `tcp://` and the rig yields identical
  `meas` payloads from the relay.
- **Health package:** the existing `bms-bridge/tests/test_logic.py` moves with the code.
- **System:** compose stack up → MPM Battery Health page shows the rig battery (manual step: API key).

## 9. Deployment story (what this buys us)

| Piece | Rig / demo | Field |
|-------|------------|-------|
| BMS | rig container | Libre Solar BMS C1 |
| Serial link | TCP | UART (`serial:`), via relay hardware |
| Relay | `wifi-sim` container | ESP32 or Cicada firmware, implementing the same mapping |
| Broker, bridge, ingestor, HA | compose | same images at a site gateway or in the cloud |
| MPM | local dev stack | the operator's MPM |

Field requirements to document, not build: MQTT auth/TLS, relay clock source, device-id provisioning,
offline buffering size on the relay.

## 10. Out of scope

Path A1 firmware (WiFi/MQTT in Zephyr); the real Cicada/ESP32 relay firmware; Prospect adapter; remote
configuration (`=Conf`) commands; validating real protection hardware (the emulator never does).

## 11. Open items (from the spike, to resolve in the plan)

1. SoC is initialised from the cells at boot, when the emulator is empty, and stays 0. Options: restart
   the firmware after the first register image; find a ThingSet command that re-initialises it; verify
   whether registers can be populated before init. Needs a quick follow-up test.
2. Temperature register units and the internal/MOSFET temperature registers need verifying.
3. `rStackVoltage_V` stayed 0 after writing register `0x34`; cause unknown (pack voltage works).
4. Read-back of FET status / balancing via `i2c read` is untested.
5. Whether `i2c` stays usable with `SHELL_CMDS_SELECT=y` (would keep `select thingset` working).
6. Shell throughput at `-rt-ratio=60` (commands per simulated second) and whether `-no-rt` is useful.
7. Image size and build time (`west update` is several GB); consider a prebuilt base image.

## 12. Migration steps (ordered, for the plan)

1. Extract `bms-health` from `bms-bridge/logic.py`; point the bridge at it; bring the bridge back into compose.
2. Remove health/summary/event from `wifi-sim`; revert its compose env (`TIME_SCALE`, `SUMMARY_INTERVAL_S`).
3. Spike follow-ups (open items 1–4) as scripts, then the encoder with unit tests.
4. Rig image + driver + TCP re-serve; golden and closed-loop tests.
5. Compose: rig replaces `bms-simulator` as the relay's `SOURCE`; time-scale wiring.
6. Docs: update `native_sim-hackathon-extension.md` (A2 relay, server-side health), README "what's missing".
