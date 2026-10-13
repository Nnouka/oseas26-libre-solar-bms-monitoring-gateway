# Quickstart: run the whole simulation

**You need:** Docker with Compose v2 (Docker Desktop on Windows or macOS, or Docker Engine plus the Compose plugin on Linux), internet access to pull images, and these host ports free: 1883, 8001, 8002. Nothing else: no Python, no `uv`, no WSL.

## 1. Start everything and check it

```bash
docker compose up --build --abort-on-container-exit --exit-code-from smoke
```

This builds the images, starts the broker, the BMS simulator and the WiFi gateway, runs an end-to-end check, and stops. The last lines are:

```text
[smoke] PASS: simulator -> gateway -> broker works end to end
```

and the exit code is `0`. Any failure prints `FAIL: ...` and exits with `1`. The first run takes a few minutes (image downloads and builds).

## 2. Or keep it running and look around

```bash
docker compose up --build -d
docker compose logs smoke                                    # shows PASS
docker compose exec mosquitto mosquitto_sub -t "bms/#" -v    # live MQTT traffic
curl -X POST localhost:8001/scenario/overheat                # trigger a fault; watch "errors" in the traffic
curl -X POST localhost:8001/scenario/normal                  # back to normal
```

| URL | What |
| --- | --- |
| <http://localhost:8001/docs> | BMS simulator API (`/live` is the serial line, `/state` shows the hidden truth) |
| <http://localhost:8002/stats> | WiFi gateway counters |

Optional Home Assistant: `docker compose --profile ha up -d`, then <http://localhost:8123> (one-time account setup; add the MQTT integration with broker `mosquitto`, port `1883`).

## 3. Remove everything

```bash
docker compose --profile ha down -v --rmi all --remove-orphans
docker builder prune -f        # optional: also clear Docker's build cache
```

This removes the containers, the network, the volumes and every image the project used.

## If something goes wrong

| Symptom | Fix |
| --- | --- |
| `port is already allocated` | copy `.env.example` to `.env` and change the port numbers |
| `'compose' is not a docker command` | install Docker Desktop or the Compose v2 plugin |
| `Cannot connect to the Docker daemon` | start Docker |
| a service is unhealthy | `docker compose logs <service>` (`mosquitto`, `bms-simulator`, `wifi-sim`) |
| `smoke` times out | `curl localhost:8002/stats` and look at `bad_crc` and `no_cells` |
| images will not download | check the network or proxy settings of Docker |
