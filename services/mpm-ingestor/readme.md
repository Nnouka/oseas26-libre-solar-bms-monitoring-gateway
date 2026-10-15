# MQTT -> MPM ingestor

Forwards `bms/<id>/summary` and `bms/<id>/event` (from the bridge) to the MicroPowerManager
plugin endpoints, with an offline outbox so nothing is lost when the network or MPM is down.

- `outbox.py`: SQLite queue. Summaries are coalesced per device (latest wins); events are kept in order.
  Survives restarts; bounded at 10,000 rows (oldest events dropped first during very long outages).
- `sender.py`: HTTP delivery with `Authorization: Bearer <MPM_API_KEY>`. 2xx = done, 400/422 = dropped
  (retrying cannot help), anything else (unreachable, 401/403, 404, 429, 5xx) = retried with exponential backoff up to 5 min.
- `ingestor.py`: MQTT client with a persistent session (QoS 1), so the broker also queues messages while this service is down.
  Each message is checked by `validate.py` before it is queued; a bad one is dropped with a log line and counted.
- `validate.py`: mirrors the MPM plugin's request rules (`IngestSummaryRequest`, `IngestEventsRequest`) and also
  requires the payload `device_id` to match the topic. Needed because MPM answers 422 for a whole event batch if one event is
  invalid, and a 422 is dropped, so one malformed event would otherwise discard up to 49 good ones.
  Event types: `fault_raised`, `fault_cleared`, `device_online`, `device_offline`.
- `main.py`: wiring + `/health` on port 8002 (queue depth, last MPM status, `received` accepted/rejected counts per
  topic, and `last_rejected` with the reason for the most recent refusal).

## Config (env)

`MQTT_HOST`, `MQTT_PORT`, `MPM_BASE_URL` (default `http://localhost:8000`; in Docker use
`http://host.docker.internal:8000`), `MPM_API_KEY` (create it in MPM: Settings > API keys), `OUTBOX_PATH`, `POLL_S`.
Without `MPM_API_KEY` messages are queued but not delivered.
`MPM_API_KEY_FILE` is the path of a file holding the key. It is read on every delivery attempt, so it may appear after start; The file wins when both are set, so a stale `MPM_API_KEY` in `.env` cannot shadow the key that `mpm-bootstrap` created. The compose `mpm` profile uses `/shared/mpm_api_key`, written by `mpm-bootstrap`.

## Test

```bash
cd services/mpm-ingestor
uv run --with pytest python -m pytest tests
```
