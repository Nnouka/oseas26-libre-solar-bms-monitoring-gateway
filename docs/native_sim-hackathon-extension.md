# Use Libre Solar `native_sim` as a baseline for the hackathon simulator

This guide answers two questions:

1. Can Libre Solar's Zephyr `native_sim` be the baseline for this hackathon's BMS simulator?
2. How should you extend the work so it feeds this repository's MQTT gateway?

**Short answer:** yes, use it as a *reference baseline* for firmware behavior, ThingSet names, units, and protocol experiments. Do not make it the battery model or the gateway's MQTT simulator. Libre Solar states that `native_sim` exercises basic firmware/ThingSet features but does not mock the complete BMS IC. The hackathon's expected first deliverable is a separate program that publishes representative BMS JSON to MQTT. So the practical plan is to run `native_sim` beside the Python simulator, and let the Python simulator be the deterministic source of battery scenarios and MQTT messages.

This keeps the valuable connection to real Libre Solar firmware without asking the native build to emulate hardware it does not emulate.

## 1. Know which simulator is which

| Piece | What it simulates | What it is for |
| --- | --- | --- |
| Libre Solar firmware + Zephyr `native_sim` | The BMS firmware application running as a host executable; basic firmware and ThingSet communication | Learn the real firmware data model and explore supported protocol behavior |
| This repository's `services/bms-simulator` | To be built: representative battery state and scenarios, published as MQTT JSON | Unblock broker, adapters, Home Assistant, Prospect, and MPM development without a physical board |
| Physical BMS C1 | Actual BQ76952, cell measurements, balancing and power switching | Hardware integration and validation on the real device |

The C1 uses an ESP32-C3 and a BQ76952 battery-monitor IC. Its ThingSet measurement interface is useful as a reference. For example, the manual shows `rPackVoltage_V`, `rPackCurrent_A`, `rSOC_pct`, `rCellVoltages_V`, `rBalancingStatus`, and temperature fields in a `?Meas` response. These are firmware/ThingSet names; they do not automatically define the gateway's MQTT JSON schema. Agree and document the MQTT contract in this repository, then map between the two explicitly.

## 2. Target architecture

Keep the battery model independent from FastAPI and MQTT so it can be run, learned from, and debugged on its own.

```text
                  reference / comparison
Libre Solar firmware ── native_sim ── ThingSet behavior and field names

Hackathon path
BatteryModel ── snapshot ── MQTT publisher ── Mosquitto ── adapters / consumers
     │                                ├── Home Assistant
     └── deterministic scenarios      ├── Prospect
                                      └── MPM ingestor (low-rate state/events)
```

At first, the two paths are separate. If later you confirm that the native simulator exposes a stable machine-readable ThingSet transport for the measurements you need, you can add an optional bridge or comparison tool. Avoid scraping firmware log text as the main simulator interface; logs are not a stable data contract.

## 3. Repository starting point

The relevant files already present are:

```text
services/bms-simulator/
├── main.py             # currently only a tiny FastAPI / and /health app
├── pyproject.toml      # FastAPI dependency; no MQTT client/model yet
├── requirements.txt
├── readme.md           # currently a placeholder
└── LEARNING_PATH.md    # staged exercises for the intended simulator
```

The learning path is aligned with the hackathon goal: learn MQTT, build the battery model separately, agree the data contract, then join the pieces. Follow it in that order. The root `docker-compose.yml` is currently empty, so this checkout does not yet define a Mosquitto service that you can connect to. You can develop and print JSON snapshots before the broker is added.

## 4. Step-by-step extension path

### Step 1: Build the firmware baseline first

Follow [native_sim-tutorial.md](native_sim-tutorial.md) to set up the Zephyr workspace and run:

```bash
west build -b native_sim -t run
```

Record what the executable actually supports on your machine: startup output, how it exits, whether a usable ThingSet interface is enabled, and any measurements the simulation provides. Do not presume that it outputs realistic live cell readings. Keep notes with the Zephyr and Libre Solar firmware revisions you built; those revisions matter when comparing behavior later.

**Checkpoint:** you can build and start/stop the firmware simulator, and you have written down what it can really demonstrate.

### Step 2: Agree the gateway message before modeling it

Draft a versioned JSON state message in the simulator's README (or a dedicated schema file) before implementing the model. Start with the values consumers need:

```json
{
  "schema": 1,
  "device_id": "demo-bms-01",
  "timestamp": "2026-10-10T12:00:00Z",
  "soc_pct": 72.4,
  "soh_pct": 98.7,
  "pack_voltage_v": 26.31,
  "pack_current_a": -4.2,
  "cell_voltages_v": [3.29, 3.28, 3.30, 3.27, 3.29, 3.28, 3.30, 3.30],
  "cell_imbalance_v": 0.03,
  "max_temperature_c": 31.2,
  "min_temperature_c": 29.8,
  "balancing_cells": [],
  "faults": [],
  "uptime_s": 120
}
```

This is a **proposal for discussion**, not a schema already implemented in the repository or guaranteed by Libre Solar. Confirm field names, units, sign convention, and required fields with the team before adapters depend on it. In particular, document whether positive current means charging or discharging. Use UTC timestamps and explicit units in names. Keep the MPM low-frequency state/event requirements separate from the higher-rate Home Assistant/Prospect telemetry.

Map the proposed fields to the C1/ThingSet reference where it makes sense. For example, `pack_voltage_v` can map to `rPackVoltage_V`, and `cell_voltages_v` to `rCellVoltages_V`. `soh_pct`, `uptime_s`, or simulator-only information may not be part of the firmware measurement response; mark those as model-derived or simulator metadata rather than pretending the BMS reports them.

**Checkpoint:** a teammate can read the message example and know what every field and unit means.

### Step 3: Learn the model without networking

Add a model module under `services/bms-simulator/`, for example:

```text
services/bms-simulator/
├── main.py
├── battery_model.py
├── scenarios.py
└── schema.md
```

Start with a plain Python class. Give it a `step(dt_seconds)` method and a `snapshot()` method. Keep its tests and console exercise independent of broker connectivity. Model a modest 8-cell LFP pack for the demo, with a small per-cell capacity/resistance variation, current profile, SoC integration, cell voltages, temperature, and fault flags. The C1 supports up to 16 series cells, but choose the simulated pack size intentionally and state it in the demo. Do not imply the model exactly reproduces the BQ76952.

Important modeling decisions to make explicit:

- Current sign convention and pack capacity.
- Cell count, chemistry, nominal capacity, and voltage limits.
- How SoC changes from current (coulomb counting) and how it is clamped.
- How cell voltage is estimated (an OCV/SoC lookup table plus an internal-resistance term is enough for a first demo).
- How temperatures move with current and ambient temperature.
- How and when a fault is raised and cleared.
- Which outputs are modeled, measured by actual hardware, or copied from firmware conventions.

Use a fixed random seed for the initial scenarios so every run starts reproducibly. Add noise only after deterministic behavior is easy to follow.

Suggested first scenarios:

1. `normal_day`: charging then discharging with plausible values.
2. `cell_drift`: one cell slowly separates from the others; show imbalance before a fault.
3. `over_temperature`: temperature crosses a configured threshold and emits one fault event.
4. `broker_disconnect`: telemetry pauses/reconnects while the simulated battery state continues.

Treat these as software demo scenarios, not safety qualification tests.

**Checkpoint:** running the model in a small local loop prints snapshots that change sensibly, without FastAPI or MQTT.

### Step 4: Have the model emit the agreed schema

Make `snapshot()` return a Python dictionary that serializes to the reviewed JSON exactly. Keep serialization and units in one place. A simplified shape is:

```python
def snapshot(self) -> dict:
    return {
        "schema": 1,
        "device_id": self.device_id,
        "timestamp": self.now_utc().isoformat().replace("+00:00", "Z"),
        "soc_pct": round(self.soc * 100, 1),
        "pack_voltage_v": round(sum(self.cell_voltages_v), 3),
        "pack_current_a": round(self.current_a, 2),
        "cell_voltages_v": [round(v, 3) for v in self.cell_voltages_v],
        "faults": list(self.active_faults),
    }
```

This is a sketch only; add the other agreed fields and ensure timestamps are timezone-aware UTC values. Do not copy a field into the payload merely because the C1 manual contains a similar name. Compute, document, and validate each value.

**Checkpoint:** the simulator emits valid JSON that matches the schema, and no MQTT connection is needed to inspect it.

### Step 5: Add MQTT as a separate adapter

Only after the snapshot and schema are stable, add an MQTT publisher module (for example `mqtt_publisher.py`) using the configured Paho MQTT client version. The publisher should:

1. Read broker host/port, device ID, topic prefix, interval, and scenario from environment variables.
2. Connect to the broker and publish the model snapshot at a controlled interval.
3. Publish availability separately and use a Last Will so consumers can detect an unexpected disconnect.
4. Publish fault transitions to an event topic so a transient fault is not lost between periodic state messages.
5. Reconnect after broker interruptions without resetting battery state.
6. Keep secrets and deployment settings out of committed source files.

Pick topic names with the team and put the final topic map in the schema document. A draft might be `bms/<device_id>/state`, `bms/<device_id>/events`, and `bms/<device_id>/availability`; those are suggestions only until agreed. Choose retain/QoS behavior deliberately and document it. Avoid publishing high-rate time-series data into MPM; MPM should get the low-frequency decision data described in the project README.

The existing Python service uses FastAPI for `/health`. Keep that endpoint responsive while the publisher runs. Use a managed lifespan/background task or another clean lifecycle pattern so shutdown closes the MQTT client and the model loop. Do not put a blocking infinite loop inside a request handler.

**Checkpoint:** `mosquitto_sub` (or another MQTT client) receives schema-valid state and event messages, and the service reports healthy.

### Step 6: Connect this to Docker Compose

The root Compose file has no services yet. When you add the broker and simulator services, make both configurations explicit and keep the app's MQTT host set to the Compose service name (for example `mosquitto`) when both containers share the Compose network. `localhost` from inside the simulator container refers to that container itself, not the broker container.

The simulator container should depend on the broker becoming healthy, but the simulator should still handle reconnects because `depends_on` is not a substitute for runtime recovery. Add a health check for the FastAPI `/health` endpoint. Publish broker ports to the host only when needed for local tools; container-to-container traffic should use the internal network.

At this stage, a useful end-to-end check is:

```text
Compose broker starts healthy
        ↓
simulator starts and /health returns OK
        ↓
MQTT subscriber receives periodic state
        ↓
triggered scenario emits a fault event
```

Use the actual service and topic names from your completed Compose/schema files; do not assume the placeholders above already exist.

### Step 7: Compare with `native_sim` without coupling the two too early

Once the Python simulator works, compare the contract to firmware references:

1. Check names, units, and definitions against the BMS C1 manual and ThingSet response examples.
2. If the native build offers a working ThingSet command path, try a read-only `?Meas` query and record the response. The manual documents this query on an actual BMS; the native simulator may not provide realistic values or all hardware-backed fields.
3. Create an explicit mapping table in the schema docs: `MQTT field` → `ThingSet field` → `unit/transform` → `source (hardware/model/derived)`.
4. Keep unavailable firmware fields as unavailable/null or clearly model-derived; do not invent a successful hardware reading.
5. If you later build a native-simulator bridge, make it optional and test it against the agreed schema. The MQTT consumers should not have to know whether a message came from a Python scenario or real hardware.

That source-neutral message contract is what lets the simulator be a useful fallback for adapters and a clear comparison target for the real BMS integration.

## 5. When should you modify the Libre Solar firmware simulator itself?

Only take this as a stretch goal after the standalone Python publisher works. Extending firmware simulation to synthesize a BQ76952 data source means finding the firmware's BMS-IC abstraction, adding a test/mock implementation for the native board, wiring mock measurements into the application, then exposing that data through ThingSet. That requires C/Zephyr knowledge and may involve upstream firmware code. It is a different and larger job from producing the hackathon's MQTT feed.

If you do pursue it:

1. Work on a fork/feature branch of `LibreSolar/bms-firmware`; keep this gateway repo's Python model separate.
2. Locate the BMS IC interface and existing unit-test fakes before changing application code.
3. Add the mock behind a build-time option so normal hardware builds keep using the real driver.
4. Make the simulated values deterministic and document every approximation.
5. Build both the native target and the real C1 target (where hardware/toolchain access permits).
6. Never describe mock behavior as validation of real protection hardware.

Do not start by patching the TI chip driver or by adding MQTT networking to the native executable. The first task has a much smaller success criterion: a stable Python model publishes the agreed JSON schema to the local broker.

## 6. A beginner-friendly definition of “done”

Call the first simulator milestone done when all of these are true:

- A newcomer can run it from the documented setup.
- It starts a predictable `normal_day` scenario without real hardware.
- It publishes the documented, versioned JSON to the local broker.
- It can demonstrate at least one cell imbalance/fault scenario.
- A subscriber can see both state and fault events.
- A broker restart does not crash the service or reset the battery model.
- `/health` accurately reports whether the service is alive (and, if useful, whether MQTT is connected).
- The docs distinguish modeled values from firmware/hardware measurements.

Then use `native_sim` as the firmware behavior reference and later plug in the physical C1 through the real transport path. That gives the hackathon team a useful simulator early, while still grounding its message contract in Libre Solar's firmware.

## References

- [Libre Solar firmware simulator docs](https://libre.solar/bms-firmware/src/dev/simulator.html)
- [Libre Solar firmware workspace setup](https://libre.solar/bms-firmware/src/dev/workspace_setup.html)
- [BMS C1 manual](https://libre.solar/bms-c1/manual/)
- [This repository's project README](../README.md)
- [Current BMS simulator learning path](../services/bms-simulator/LEARNING_PATH.md)
- [Current BMS simulator service](../services/bms-simulator/)
