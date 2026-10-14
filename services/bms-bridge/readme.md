# BMS bridge

Turns the raw `bms/<id>/meas` stream into what operators and MPM actually need.
Works the same for the simulator and a real BMS. Contract: `docs/mqtt-schema.md`.

| In | Out |
|---|---|
| `bms/+/meas` (every few s) | `bms/<id>/summary` (retained; every `SUMMARY_INTERVAL_S`, and immediately when faults/health change) |
| `bms/+/info` (capacity) | `bms/<id>/event` (fault raised/cleared, device online/offline, with plain-language message + action) |
| `bms/+/status` (Last Will) | Home Assistant discovery for health, health score, reasons, SoH, cycles |

## Files

- Health logic lives in the shared package `packages/bms-health` (health score, cycle counting, fault events, fault catalog). No MQTT.
- `bridge.py`: MQTT wiring and the summary timer. `discovery.py`: HA entities. `main.py`: entry point + `/health` on port 8001.
- Tests for the health logic: `packages/bms-health/tests`.

## Config (env)

`MQTT_HOST`, `MQTT_PORT`, `SUMMARY_INTERVAL_S` (900 in production), `TIME_SCALE`
(1 for a real BMS; equals the simulator `SPEED` in the demo stack, so cycle counting uses simulated time).

## Run tests

```bash
cd services/bms-bridge
uv run --with pytest python -m pytest tests
```

## Notes / limits

- SoH is a **cycle-based estimate** (see schema doc), not a measurement.
- State is in memory: cycle count restarts from 0 if the bridge restarts. Persisting it (or taking it from MPM) is a TODO.
- Buffering for bad networks belongs in the MPM ingestor (next service).
