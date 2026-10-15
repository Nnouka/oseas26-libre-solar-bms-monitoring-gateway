# Firmware rig, dumb relay and server-side health: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native, in this session). Steps use checkbox syntax. Every production change needs a failing test first (superpowers:test-driven-development).

**Goal:** A rig container that runs the real Zephyr `native_sim` firmware fed by our battery model, a relay that only forwards, and one shared health package used by the bridge.

**Architecture:** `bms-health` (pure Python) is extracted from `bms-bridge/logic.py`. `wifi-sim` loses its health code again. A new `services/firmware-rig` encodes `ChipReading` into BQ76952 register writes, sends them through the firmware shell (`i2c write`), reads FET/balancing state back, and re-serves the firmware's `#mLive` line over TCP.

**Tech Stack:** Python 3.12, pytest, paho-mqtt, FastAPI (rig API), Zephyr `native_sim` (32-bit Linux, built with west), Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-14-firmware-rig-and-a2-relay-design.md`

## Global Constraints

- Production parity: only the relay's `SOURCE` differs between rig and a real C1.
- No scenario field in MQTT/MPM (`bms/<id>/summary|event` carry none).
- Rig register map (direct commands): cells `0x14+2i` (mV, int16 LE); stack voltage `0x34` (10 mV); current `0x3A` (10 mA, + = charging); internal temp `0x68`; thermistor TS1 `0x70`; DCHG (MOSFET) `0x78`; temperatures in 0.1 K.
- FET control is subcommand `0x0097` (bit0 DSG_OFF, bit1 PDSG_OFF, bit2 CHG_OFF, bit3 PCHG_OFF, **1 = off**); active balancing is subcommand `0x0083` (u16 cell mask).
- Firmware needs `CONFIG_I2C_SHELL=y` and `CONFIG_SHELL_CMDS_SELECT=n`; ThingSet requests are `thingset ?Meas`.
- Tests that need the firmware binary read `RIG_BINARY` and skip without it. They run in WSL (`~/spike/build/zephyr/zephyr.exe`).
- Windows host: run Python tests with `uv run --with pytest python -m pytest tests` from each service folder.

## Review Focus

- Negative current and negative temperatures encode as signed int16 (a discharging battery, a cold site).
- Unused cell channels must be written as 0 mV so the firmware sees "no cell" (same sentinel as `NOT_CONNECTED_V`).
- A cell above 32.767 V or a current beyond ±327 A must clamp, not wrap.
- The firmware process dying mid-run must not hang the TCP re-serve; clients get a clean disconnect.
- A health summary must be identical whether `meas` arrived via the rig or via a recorded fixture.

---

### Task 1: `bms-health` package; bridge uses it

**Files:**
- Create: `packages/bms-health/pyproject.toml`, `packages/bms-health/bms_health/__init__.py`, `packages/bms-health/tests/test_logic.py` (moved)
- Move: `services/bms-bridge/logic.py` → `packages/bms-health/bms_health/logic.py`
- Modify: `services/bms-bridge/bridge.py:13`, `services/bms-bridge/pyproject.toml`, `services/bms-bridge/Dockerfile`, `docker-compose.yml` (bridge service, build context = repo root)
- Delete: `services/bms-bridge/tests/test_logic.py` after the move

**Interfaces:** Produces `from bms_health import DeviceTracker, assess_health, FAULTS, fault_info` with the current signatures.

- [ ] **Step 1 (RED):** copy `services/bms-bridge/tests/test_logic.py` to `packages/bms-health/tests/test_logic.py`, changing `from logic import ...` to `from bms_health import ...`. Run `uv run --with pytest python -m pytest packages/bms-health/tests` (with `PYTHONPATH=packages/bms-health`). Expected: ImportError.
- [ ] **Step 2 (GREEN):** `git mv services/bms-bridge/logic.py packages/bms-health/bms_health/logic.py`; `__init__.py` re-exports the four names. Add `pyproject.toml` (name `bms-health`, no dependencies). Rerun: all tests pass.
- [ ] **Step 3:** update `bridge.py` to `from bms_health import DeviceTracker`; add the path dependency to `services/bms-bridge/pyproject.toml`; change the Dockerfile to `COPY packages/bms-health` and `COPY services/bms-bridge` with context at the repo root. Run the bridge test suite and `docker compose build bms-bridge`.
- [ ] **Step 4:** delete the old bridge test copy, commit.

### Task 2: Remove health from `wifi-sim` (the relay is a dumb pipe)

**Files:** Modify `services/wifi-sim/relay.py`, `main.py`, `schema.py`, `tests/test_relay.py`; delete `services/wifi-sim/health.py`; modify `docker-compose.yml` (remove `TIME_SCALE`, `SUMMARY_INTERVAL_S` from wifi-sim).

- [ ] **Step 1 (RED):** add test `test_relay_publishes_only_meas_and_mpm_never_summary_or_event`: feed `GOOD` (carries a fault) through a connected relay and assert no topic ends in `/summary` or `/event`. Run: fails (relay still publishes both).
- [ ] **Step 2 (GREEN):** remove `_track_health`, `_flush_events`, tracker, `Summary`/`Event` schemas, env vars and `health.py`; delete the five tests added on 2026-10-14 and restore the two exact-list assertions to `["bms/dev1/mpm", "bms/dev1/meas"]`. Run `uv run --with pytest python -m pytest tests`: all pass.
- [ ] **Step 3:** commit.

### Task 3: Register encoder (pure)

**Files:** Create `services/firmware-rig/encoder.py`, `services/firmware-rig/tests/test_encoder.py`, `services/firmware-rig/tests/conftest.py`. Copy `chip.py` (unchanged, `ChipReading`) into the rig.

**Interfaces:** Produces `encode(reading: ChipReading) -> list[tuple[int, bytes]]` (register, payload; contiguous registers merged) and `i2c_write_cmd(reg: int, payload: bytes, bus="i2c@100", addr=0x08) -> str`.

- [ ] **Step 1 (RED):** tests, one behaviour each:
  - `test_cell_voltages_are_millivolts_little_endian`: `ChipReading(cell_v=[3.300]+[0.0]*15, ...)` → first write is `(0x14, bytes.fromhex("e40c") + b"\0"*30)`.
  - `test_unused_channels_are_zero_millivolts`
  - `test_charging_current_is_positive_in_10ma_units`: 1.0 A → `0x3A` payload `64 00`.
  - `test_discharge_current_is_signed`: −2.5 A → int16 `-250`.
  - `test_temperatures_are_tenths_of_kelvin_with_thermistor_and_mosfet_registers`: 25.0 °C → `int((25+273.15)*10)` at `0x70`; MOSFET at `0x78`; IC at `0x68`.
  - `test_values_clamp_instead_of_wrapping`: 40 V cell and 400 A saturate at int16 limits.
  - `test_stack_voltage_is_sum_in_10mv`
  - `test_i2c_write_cmd_format`: `"i2c write i2c@100 0x8 0x14 0xe4 0x0c"`.
  - `test_long_payloads_are_split_to_the_shell_buffer_size`: no command carries more than 16 data bytes.
- [ ] **Step 2:** run, expect ImportError/failures. **Step 3:** implement minimally. **Step 4:** run, expect all pass. **Step 5:** commit.

### Task 4: Firmware process client (shell + uart_1)

**Files:** Create `services/firmware-rig/firmware.py`, `tests/test_firmware_client.py`, `tests/test_firmware_integration.py`.

**Interfaces:** Produces `class FirmwareProcess(binary: str, rt_ratio: float = 1.0)` with `start()`, `stop()`, `alive`, `shell(cmd: str, timeout=2.0) -> list[str]`, `write(reg: int, payload: bytes)`, `read_direct(reg: int, n: int) -> bytes`, `read_subcmd(code: int) -> int` (two writes to `0x3E` then reads `0x40`), `live_lines` (a queue of `#mLive` lines from `uart_1`), `uart1_path`.

- [ ] **Step 1 (RED):** unit tests with a stand-in executable (`tests/fake_firmware.py`, a script that prints `uart_1 connected to pseudotty: <path>`, echoes shell commands, answers `i2c read`): `test_shell_echo_and_read_parsing`, `test_dead_process_raises_on_shell`, `test_uart1_path_is_discovered_from_boot_log`.
- [ ] **Step 2 (GREEN):** implement with `subprocess.Popen` pipes and a reader thread (as in the spike scripts). Unit tests pass.
- [ ] **Step 3 (RED, integration, skipped without `RIG_BINARY`):** write a fixed `ChipReading` through `write`, assert a `#mLive` line shows `rCellVoltages_V` starting `[3.300,` and `rPackCurrent_A` 1.00; assert `read_direct(0x14, 2) == b"\xe4\x0c"`. Run in WSL: `RIG_BINARY=~/spike/build/zephyr/zephyr.exe python3 -m pytest …` (needs `pytest` in a WSL venv).
- [ ] **Step 4:** commit.

### Task 5: Closed loop, TCP re-serve, rig API

**Files:** Create `services/firmware-rig/rig.py`, `main.py`, `tests/test_rig.py`; copy `battery.py`, `chemistry.py` from `bms-simulator`.

**Interfaces:** Consumes `FirmwareProcess`, `encode`. Produces `class Rig(firmware, battery, interval_s, speed)` with `tick()` (step model → encode → write → read back FET and balancing → store `chg_on`, `dis_on`, `bleed_mask`) and a TCP server on `RIG_TCP_PORT` (default 3333) that sends every `#mLive` line to each connected client.

- [ ] **Step 1 (RED):** `test_tick_writes_the_battery_reading_to_the_firmware`, `test_fet_off_flag_stops_charging_in_the_next_step` (fake firmware returns `FET_CONTROL = 0b100`; assert the model gets `chg_on=False`), `test_balancing_mask_is_fed_back`, `test_tcp_clients_receive_live_lines`, `test_dead_firmware_closes_clients_cleanly`.
- [ ] **Step 2–4:** implement, pass. **Step 5 (integration):** with the real binary, drive `overheat` for a few hundred simulated seconds with `rt_ratio` high and assert `CHG_OVERTEMP` appears in `rErrorFlags`.
- [ ] **Step 6:** FastAPI `main.py`: `/health` (firmware alive, ticks, shell errors), `/state`, `POST /scenario/{name}`. Test with `TestClient`. Commit.

### Task 6: Image, compose, docs

**Files:** Create `services/firmware-rig/Dockerfile`, `services/firmware-rig/rig.conf`, `services/firmware-rig/readme.md`; modify `docker-compose.yml`, `docs/native_sim-hackathon-extension.md`, `README.md`, `.env.example`.

- [ ] **Step 1:** Dockerfile (multi-stage): stage 1 installs west + build deps and runs `west build -b native_sim -- -DEXTRA_CONF_FILE=rig.conf` with `ZEPHYR_TOOLCHAIN_VARIANT=host` (32-bit multilib); stage 2 copies `zephyr.exe` into a slim Python image with the rig code. Build it and fix what breaks (this is where the several-GB `west update` happens; document the build time).
- [ ] **Step 2:** compose: `firmware-rig` service (profile-free), `wifi-sim.SOURCE=tcp://firmware-rig:3333`, `bms-bridge` back in the stack with `TIME_SCALE=${SPEED:-60}`, remove `bms-simulator` from the default flow (keep it behind a `sim` profile as the pure-Python fallback).
- [ ] **Step 3:** docs: A2 topology, field table from the spec, "what we're missing", `.env` example. 
- [ ] **Step 4:** `docker compose up -d --build`; verify `meas`, `summary`, `event` appear on the broker and the ingestor accepts them. Commit.

## Self-review

Spec coverage: §5.1–5.2 → Tasks 3–5; §5.3 → Task 2; §5.4–5.5 → Task 1; §6 timing → Tasks 5–6 (`rt_ratio`, `TIME_SCALE`); §7 errors → Tasks 4–5; §8 tests → each task; §9 deployment table → Task 6 docs; §11 open items 1–6 are probed inside Tasks 4–5 integration tests (SoC init and stack voltage are handled where they surface and recorded in the readme); item 7 (image size) is Task 6 step 1.
Type consistency: `encode`/`i2c_write_cmd` (Task 3) are consumed by `FirmwareProcess.write` and `Rig.tick` (Tasks 4–5) with the same names.
