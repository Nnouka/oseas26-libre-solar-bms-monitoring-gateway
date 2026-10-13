# Part 11: Docker Compose

**Goal.** Put the broker, the simulator and the WiFi gateway (and, optionally, Home Assistant) into containers and start them all with one command, `docker compose up`, in a way the rest of the team can reuse for the other services later.

**Time.** 2 hours.

> **Honesty note.** The Dockerfile and Compose file below were written carefully but **could not be run** in the environment where this tutorial was prepared (no Docker daemon). The Python service they run was tested. Treat the first
> `docker compose up --build` as the real test, and read the error messages: the cause is almost always a path or an indentation.

## Ideas

- **Image vs container.** An *image* is a packaged filesystem plus a start command, built from a `Dockerfile`. A *container* is a running copy of an image.
- **Layers and caching.** Each Dockerfile instruction is a cached layer. Copy the dependency files (`pyproject.toml`, `uv.lock`) and install **before** copying your source, so editing a `.py` file does not reinstall everything.
- **Compose networks.** Services in one Compose file can reach each other by *service name*. Inside the simulator container `localhost` means the simulator itself, so the broker is `mosquitto`, not `localhost`. This is the number one beginner mistake.
- **Healthchecks and `depends_on`.** A container is "started" long before the program in it is "ready". A healthcheck is a command Docker runs repeatedly. `depends_on: condition: service_healthy` waits for it to succeed.
  But dependencies do not replace reconnect logic (Part 7), because brokers restart at runtime too.
- **Ports.** `"8001:8000"` means host port 8001 maps to container port 8000. Containers talk to each other over the internal network and need no published ports. You publish ports only so *you* can reach them from your browser or tools.

## Steps

### 11.1 The Dockerfile

`services/bms-simulator/Dockerfile`:

```dockerfile
FROM python:3.12-slim

# uv is only needed at build time, to install exactly what uv.lock says
RUN pip install --no-cache-dir uv

WORKDIR /app

# 1) dependencies first (cached unless pyproject.toml or uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) then the code
COPY *.py ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Why `--host 0.0.0.0`? `uvicorn` binds to `127.0.0.1` by default, which inside a container is reachable only from inside the container. `0.0.0.0` means "all network interfaces", so the published port works.
Why `PYTHONUNBUFFERED`? So log lines appear immediately in `docker compose logs`.

Add `services/bms-simulator/.dockerignore` so that the build context stays small and your local virtual environment is not copied in:

```text
.venv
__pycache__
.pytest_cache
tests
spikes
dev_*.py
tutorial
```

(`COPY *.py` would copy `dev_*.py` into the image; the ignore file keeps them out.)

Test the image on its own first:

```bash
docker build -t bms-simulator services/bms-simulator
docker run --rm -p 8001:8000 -e MQTT_HOST=host.docker.internal bms-simulator
# or without MQTT at all: add  -e MQTT_ENABLED=false
```

Open <http://localhost:8001/health>. Seeing `"mqtt_connected": true` means the container reached the broker you still have running from Part 2.

### 11.2 The Compose file

Replace the empty root `docker-compose.yml`:

```yaml
services:
  mosquitto:
    image: eclipse-mosquitto:2.0.20
    ports:
      - "1883:1883"
    volumes:
      - ./infra/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
    healthcheck:
      # publishing succeeds only if the broker accepts connections
      test: ["CMD-SHELL", "mosquitto_pub -h localhost -t healthcheck/ping -m ok -q 1"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped

  bms-simulator:
    build: ./services/bms-simulator
    environment:
      MQTT_HOST: mosquitto
      MQTT_PORT: "1883"
      MQTT_ENABLED: "false"       # the gateway publishes, the simulator only plays the BMS
      PUBLISH_INTERVAL_S: "5"
      SPEED: "60"
      SCENARIO: normal
    ports:
      - "8001:8000"
    depends_on:
      mosquitto:
        condition: service_healthy
    healthcheck:
      # the slim image has no curl, but it has Python
      test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s
    restart: unless-stopped
```

Add the gateway as a third service, below `bms-simulator`. It reads the simulator's `/live` endpoint over the Compose network and is the **only** publisher (production parity). It therefore takes over the device id `haske-bms-001`, and the simulator must not publish:

```yaml
  wifi-sim:
    build: ./services/wifi-sim
    environment:
      SOURCE: http://bms-simulator:8000/live
      MQTT_HOST: mosquitto
      DEVICE_ID: haske-bms-001
      PUBLISH_INTERVAL_S: "5"
    ports:
      - "8002:8000"
    depends_on:
      mosquitto:
        condition: service_healthy
      bms-simulator:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s
    restart: unless-stopped
```

It needs its own `Dockerfile` and `.dockerignore`: copy them from `services/bms-simulator` unchanged. Note that `SOURCE` points at the **service name** `bms-simulator` and the container port `8000`, not at `localhost:8001`.

Stop your hand-run broker (`docker stop mosq`) so port 1883 is free, then:

```bash
docker compose up --build
```

Expected in the logs (order may vary): the broker starts, the simulator waits for it to become healthy, then `connected to mosquitto:1883`. In a second terminal:

```bash
docker compose ps                    # all three services should say "healthy"
curl localhost:8001/health
curl localhost:8002/stats
docker compose exec mosquitto mosquitto_sub -t "bms/#" -v
```

### 11.3 Add Home Assistant (optional but recommended)

Add this service (use the pinned version you chose in Part 8) and a volume:

```yaml
  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:<YYYY.M.P>
    ports:
      - "8123:8123"
    volumes:
      - ha-config:/config
    depends_on:
      mosquitto:
        condition: service_healthy
    restart: unless-stopped

volumes:
  ha-config:
```

Merge the `volumes:` key at the end of the file once. In HA the MQTT broker is now `mosquitto`, port 1883. Pin the version: `:stable` changes under you, and an unannounced change on the morning of the hackathon is a bad surprise.

### 11.4 Compose profiles for the future services

The full stack will contain mpm-ingestor, prospect-adapter and cicada-relay-sim. Agree with the team on the rule *"health endpoint first"*: each service ships a container with a `/health` endpoint and a compose entry before it has any feature. A `profiles:` key lets
a service be optional, for example `profiles: ["ha"]` on `homeassistant`, started with `docker compose --profile ha up`.

### 11.5 Break it on purpose

1. `docker compose stop mosquitto`, then watch `docker compose logs -f wifi-sim`: the gateway logs the disconnect, but `docker compose ps` still shows it `healthy`. Why is that right?
2. `docker compose start mosquitto`. The gateway reconnects, republishes `online`, `info` and the discovery topics, and `soc_pct` carries on (the simulator never stopped).
3. `docker compose down` then `docker compose up`. The `bms/<id>/status` retained message is gone with the broker (we configured `persistence false`), but the gateway publishes it again when it connects.
4. Change `MQTT_HOST` of `wifi-sim` to `localhost` in the compose file and recompose. What happens? Read the logs and explain it in one sentence.

<details>
<summary>What you should see</summary>

1. The health endpoint reports the *service*, not the broker. Restarting the gateway because the broker is down would add disruption without fixing anything.
3. That is the benefit of publishing the retained topics in `on_connect`.
4. `connection refused` retries forever: `localhost` inside the container is the container itself, with no broker. The service stays healthy but never connects. This is the exact case that `mqtt_connected` in `/health` makes visible.

</details>

## Checkpoint

- `docker compose up --build` brings the stack up, all services are healthy, and `mosquitto_sub -t "bms/#"` shows `status`, `info`, `meas` (and `mpm` on changes and heartbeats) for `haske-bms-001`, all published by the gateway, while `curl localhost:8001/health` says `"mqtt_enabled": false`.
- Another team member can clone the repository and do the same with no steps beyond `docker compose up --build`. (Ask one to try. Their problems are bugs in your instructions.)

## Commit

```bash
git add docker-compose.yml infra services
git commit -m "feat: dockerfile and compose stack for broker and simulator"
```

Next: [Part 12: Compare and wrap up](12-compare-and-wrap-up.md), then [Part 13: One command up, one command down](13-one-command.md). Do Part 13 before you tell anyone the work is finished.
