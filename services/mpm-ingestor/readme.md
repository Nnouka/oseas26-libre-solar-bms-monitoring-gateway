# MQTT -> MPM ingestor

Forwards `bms/<id>/summary` and `bms/<id>/event` (from the bridge) to the MicroPowerManager
plugin endpoints, with an offline outbox so nothing is lost when the network or MPM is down.

- `outbox.py`: SQLite queue. Summaries are coalesced per device (latest wins); events are kept in order.
  Survives restarts; bounded at 10,000 rows (oldest events dropped first during very long outages).
- `sender.py`: HTTP delivery with `Authorization: Bearer <MPM_API_KEY>`. 2xx = done, 400/422 = dropped
  (retrying cannot help), anything else (unreachable, 401/403, 404, 429, 5xx) = retried with exponential backoff up to 5 min.
- `ingestor.py`: MQTT client with a persistent session (QoS 1), so the broker also queues messages while this service is down.
- `main.py`: wiring + `/health` on port 8002 (queue depth, last MPM status).

## Config (env)

`MQTT_HOST`, `MQTT_PORT`, `MPM_BASE_URL` (default `http://localhost:8000`; in Docker use
`http://host.docker.internal:8000`), `MPM_API_KEY` (create it in MPM: Settings > API keys), `OUTBOX_PATH`, `POLL_S`.
Without `MPM_API_KEY` messages are queued but not delivered.

## Test

```bash
cd services/mpm-ingestor
uv run --with pytest python -m pytest tests
```
