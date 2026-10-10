# BMS simulator

Publishes realistic Libre Solar BMS telemetry to MQTT (schema: `docs/mqtt-schema.md`) and serves a
small control API. Model code (`battery.py`) has no MQTT/web code; `publisher.py` handles MQTT;
`discovery.py` builds Home Assistant auto-discovery messages.

## Run with Docker (recommended)

From the repo root: `docker compose up --build`

## Run locally (needs a broker on localhost:1883, e.g. `docker compose up mosquitto`)

```bash
uv sync
uv run fastapi dev main.py          # works on Windows, macOS, Linux
```

## Configuration (env vars)

`MQTT_HOST`, `MQTT_PORT`, `DEVICE_ID`, `PUBLISH_INTERVAL_S` (real seconds), `SPEED`
(simulated seconds per real second; 60 = a day in 24 min), `SCENARIO`.

## Scenarios

`normal`, `cell_drift` (a weak cell -> imbalance -> `CELL_FAILURE`), `overheat` (cooling failure ->
`CHG_OVERTEMP` then `DIS_OVERTEMP`). Switch live: `POST /scenario/overheat`
(or use `/docs`). `GET /state` shows the current values, `GET /health` the service + broker link.
