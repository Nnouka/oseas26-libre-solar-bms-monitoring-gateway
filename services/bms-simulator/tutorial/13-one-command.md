# Part 13: One command up, one command down

**Goal.** Anyone, on any machine that is not yours, can start the whole simulation with one command, see a pass/fail result, and remove every trace with one command. This is what graders will do. It decides whether your work counts, so it gets its own part.

**Time.** 2 hours, plus a rehearsal on a second machine.

> **Status of this part.** `docker compose config` validated the compose file, and the smoke test passed against the real services started as plain processes with a real Mosquitto broker. The image builds and the container networking could **not** be run where this was written (no Docker daemon). That is why 13.6 is a mandatory rehearsal on another machine.

## Ideas

**Assume nothing about the other machine.** The grader has Docker with Compose v2 and an internet connection. They do not have Python, `uv`, Mosquitto, WSL, your folders, your ports or your line endings. Every design choice below removes one assumption:

| Assumption you must not make | What protects you |
| --- | --- |
| Python or `uv` is installed | Everything builds inside images (`uv sync` runs in the Dockerfile) |
| Port 1883, 8001 or 8002 is free | Host ports are variables with defaults (`${BROKER_PORT:-1883}`), changed in `.env` |
| The repo is checked out with Linux line endings | `.gitattributes` forces LF (a CRLF `mosquitto.conf` or script breaks inside Linux containers) |
| Images tagged `latest` behave as they did for you | Pin versions (the broker is pinned; pin Home Assistant before the hackathon) |
| Someone watches the logs | A `smoke` service checks the whole chain and exits 0 or 1 |
| `docker compose down` removes everything | Fixed project name, named volumes only, `--rmi all` (see 13.5) |
| It is the only project on that Docker | The project name `oseas-bms` means `down` can only touch this stack |

**A test is the grader's eyes.** "The containers started" proves little. The `smoke` service checks the chain the way a user would see it: the gateway announces itself, a late subscriber receives everything from retained messages, measurements follow the schema, the gateway is the only publisher, and a triggered fault arrives. It runs once and exits with a status code, which a script or CI job can use.

**A lesson the smoke test taught while it was written.** The first version counted Home Assistant discovery messages by their *retain flag* and failed. A message the broker forwards **live** to an already-connected subscriber has the flag cleared. Only a client that connects *later* sees `retain=1`. So the test now opens a second, fresh "late joiner" connection, which is exactly the situation of Home Assistant starting after the devices. It tests what retained messages are *for*.

## Steps

### 13.1 The final compose file

Replace the compose file from Part 11 with this one. Read the comments. Every line is there for a reason.

```yaml
name: oseas-bms        # a fixed project name: "down" can only ever touch this stack

services:
  mosquitto:
    image: eclipse-mosquitto:2.0.20
    ports:
      - "${BROKER_PORT:-1883}:1883"
    volumes:
      - ./infra/mosquitto/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
    healthcheck:
      test: ["CMD-SHELL", "mosquitto_pub -h localhost -t healthcheck/ping -m ok -q 1"]
      interval: 3s
      timeout: 3s
      retries: 10

  # plays the BMS: produces the serial #mLive line on /live, publishes nothing to MQTT itself
  bms-simulator:
    build: ./services/bms-simulator
    environment:
      MQTT_ENABLED: "false"
      PUBLISH_INTERVAL_S: "2"
      SPEED: "${SPEED:-300}"
      SCENARIO: normal
    ports:
      - "${SIMULATOR_PORT:-8001}:8000"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)"]
      interval: 3s
      timeout: 5s
      retries: 10
      start_period: 5s

  # the gateway: serial line in, MQTT (with Home Assistant discovery) out
  wifi-sim:
    build: ./services/wifi-sim
    environment:
      SOURCE: http://bms-simulator:8000/live
      MQTT_HOST: mosquitto
      DEVICE_ID: haske-bms-001
      PUBLISH_INTERVAL_S: "2"
    ports:
      - "${GATEWAY_PORT:-8002}:8000"
    depends_on:
      mosquitto:
        condition: service_healthy
      bms-simulator:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)"]
      interval: 3s
      timeout: 5s
      retries: 10
      start_period: 5s

  # runs once, checks the whole chain, exits 0 (pass) or 1 (fail)
  smoke:
    build: ./services/smoke
    environment:
      MQTT_HOST: mosquitto
      DEVICE_ID: haske-bms-001
      SIM_URL: http://bms-simulator:8000
      GATEWAY_URL: http://wifi-sim:8000
    depends_on:
      wifi-sim:
        condition: service_healthy
    restart: "no"

  # optional: docker compose --profile ha up -d   (first start needs a one-time click-through in the browser)
  homeassistant:
    profiles: ["ha"]
    image: ghcr.io/home-assistant/home-assistant:stable   # PIN to a YYYY.M.P tag before the hackathon
    ports:
      - "${HA_PORT:-8123}:8123"
    volumes:
      - ha-config:/config
    depends_on:
      mosquitto:
        condition: service_healthy

volumes:
  ha-config:
```

Differences from Part 11: the fixed `name:`, configurable host ports, the simulator with `MQTT_ENABLED: "false"` (production parity: the gateway is the only publisher), `SPEED` of 300 (one simulated day in about five minutes, so an overheat fault shows up in about twenty seconds), the `smoke` service, and Home Assistant behind a profile so it is not started (or pulled) by default.

### 13.2 `.env.example`

```text
# Copy to .env only if one of these host ports is already used on your machine.
BROKER_PORT=1883
SIMULATOR_PORT=8001
GATEWAY_PORT=8002
HA_PORT=8123
SPEED=300
```

Nobody has to create `.env` unless a port is taken. Add `.env` to `.gitignore`.

### 13.3 The smoke test

`services/smoke/Dockerfile`:

```dockerfile
FROM python:3.12-slim
RUN pip install --no-cache-dir "paho-mqtt>=2.1,<3"
COPY smoke.py /smoke.py
CMD ["python", "/smoke.py"]
```

`services/smoke/smoke.py`. Write it yourself from this specification, then compare. It has five checks, each printing a line, and any failure ends the program with `FAIL: ...` and exit code 1.

1. **Wait** (up to `TIMEOUT_S`) until the simulator's `/health` says `ok` and the gateway's `/health` says `mqtt_connected`. Then connect to the broker and subscribe to `bms/<id>/#`.
2. **Retained snapshot.** Open a *second* connection, subscribe to `bms/<id>/#` and `homeassistant/#`, wait three seconds, and collect the topics whose `retain` flag is set. Require `status`, `info` and at least 20 discovery topics for this device.
3. **Measurements.** Wait for three `meas` messages. Check the required keys, `schema == 1` and `0 <= soc_pct <= 100`.
4. **Single publisher.** The simulator's `/health` must report `mqtt_enabled: false`.
5. **A fault arrives.** `POST /scenario/overheat` on the simulator and wait until some `meas` message contains `CHG_OVERTEMP` in `errors`. Then `POST /scenario/normal`.

Use only `urllib.request` and `paho-mqtt`. Read the settings from the environment: `MQTT_HOST`, `MQTT_PORT`, `DEVICE_ID`, `SIM_URL`, `GATEWAY_URL`, `TIMEOUT_S`, `EXPECTED_ENTITIES`.

Before containerising, run it against the services started by hand (as in Parts 9 and 10, with `MQTT_ENABLED=false` on the simulator): `SIM_URL=http://localhost:8000 GATEWAY_URL=http://localhost:8002 uv run --with paho-mqtt python services/smoke/smoke.py`.

<details>
<summary>Solution: smoke.py</summary>

```python
"""End-to-end smoke test: BMS simulator -> WiFi gateway -> MQTT broker.

Exit code 0 = everything works. Any other exit code = something failed, and the last line says what.
Needs only the Python standard library and paho-mqtt.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request

import paho.mqtt.client as mqtt

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DEVICE_ID = os.getenv("DEVICE_ID", "haske-bms-001")
SIM_URL = os.getenv("SIM_URL", "http://localhost:8001")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8002")
TIMEOUT_S = float(os.getenv("TIMEOUT_S", "120"))
EXPECTED_ENTITIES = int(os.getenv("EXPECTED_ENTITIES", "20"))

BASE = f"bms/{DEVICE_ID}"
lock = threading.Lock()
seen = {"status": None, "info": None, "meas": []}


def http(method: str, url: str):
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read().decode()


def step(text: str) -> None:
    print(f"[smoke] {text}", flush=True)


def fail(text: str) -> None:
    print(f"[smoke] FAIL: {text}", flush=True)
    sys.exit(1)


def wait_for(description: str, condition, timeout_s: float = TIMEOUT_S):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            result = condition()
            if result:
                return result
        except OSError:
            pass
        time.sleep(1)
    fail(f"timed out after {timeout_s:.0f} s waiting for: {description}")


def on_connect(client, userdata, flags, reason_code, properties=None):
    client.subscribe(f"{BASE}/#", 1)


def on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="replace")
    with lock:
        if msg.topic == f"{BASE}/status":
            seen["status"] = payload
        elif msg.topic == f"{BASE}/info":
            seen["info"] = json.loads(payload)
        elif msg.topic == f"{BASE}/meas":
            seen["meas"].append(json.loads(payload))


def retained_snapshot() -> set[str]:
    """What a client that connects NOW is handed from the broker's memory (messages with the retain flag)."""
    topics: set[str] = set()
    late = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smoke-late-joiner")
    late.on_connect = lambda c, u, f, rc, p=None: c.subscribe([(f"{BASE}/#", 1), ("homeassistant/#", 1)])
    late.on_message = lambda c, u, m: topics.add(m.topic) if m.retain and DEVICE_ID in m.topic else None
    late.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    late.loop_start()
    time.sleep(3)
    late.loop_stop()
    late.disconnect()
    return topics


def main() -> None:
    step(f"waiting for the simulator at {SIM_URL} and the gateway at {GATEWAY_URL}")
    wait_for("simulator /health", lambda: json.loads(http("GET", f"{SIM_URL}/health"))["status"] == "ok")
    wait_for("gateway /health", lambda: json.loads(http("GET", f"{GATEWAY_URL}/health"))["mqtt_connected"])

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="smoke-test")
    client.on_connect, client.on_message = on_connect, on_message
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_start()

    step("1/5 the gateway announced itself (retained status 'online' and info)")
    wait_for("status online", lambda: seen["status"] == "online")
    wait_for("info message", lambda: seen["info"])
    info = seen["info"]
    if info["device_id"] != DEVICE_ID or info["cell_count"] < 1:
        fail(f"unexpected info message: {info}")

    step(f"2/5 a late joiner gets status, info and {EXPECTED_ENTITIES} Home Assistant entities from retained messages")
    topics = wait_for("retained messages", lambda: (lambda t: t if len(t) >= EXPECTED_ENTITIES + 2 else None)(retained_snapshot()), 30)
    for needed in (f"{BASE}/status", f"{BASE}/info"):
        if needed not in topics:
            fail(f"{needed} is not retained")

    step("3/5 measurements flow and follow the schema")
    wait_for("three meas messages", lambda: len(seen["meas"]) >= 3)
    required = {"schema", "device_id", "ts", "state", "pack_voltage_v", "pack_current_a", "soc_pct",
                "cell_voltages_v", "cell_temps_c", "mosfet_temp_c", "balancing_mask", "errors"}
    with lock:
        last = seen["meas"][-1]
    missing = required - set(last)
    if missing or last["schema"] != 1 or not 0 <= last["soc_pct"] <= 100:
        fail(f"bad meas message (missing {sorted(missing)}): {last}")

    step("4/5 the simulator publishes nothing itself (the gateway is the only publisher)")
    health = json.loads(http("GET", f"{SIM_URL}/health"))
    if health.get("mqtt_enabled") is not False:
        fail(f"simulator should run with MQTT_ENABLED=false, got {health}")

    step("5/5 trigger the overheat scenario and wait for the over-temperature fault")
    http("POST", f"{SIM_URL}/scenario/overheat")
    wait_for("CHG_OVERTEMP in the errors list",
             lambda: any("CHG_OVERTEMP" in m["errors"] for m in list(seen["meas"])))
    http("POST", f"{SIM_URL}/scenario/normal")

    client.loop_stop()
    client.disconnect()
    step("PASS: simulator -> gateway -> broker works end to end")


if __name__ == "__main__":
    main()
```

Note `wait_for`: it retries a condition once a second and treats a network error as "not yet", so the test tolerates services that come up in any order. Note also `sys.exit(1)` inside `fail`, so no check can be skipped silently.

</details>

### 13.4 The repository must be clean to clone

Check each of these on a fresh clone, not in your working copy:

- `.gitattributes` exists at the root (`* text=auto eol=lf`). A shell script or config file with CRLF fails in a Linux container with an error like `bad interpreter` or a config parse error.
- `uv.lock` is committed in both `services/bms-simulator` and `services/wifi-sim` (the Dockerfile uses `--frozen`).
- `.dockerignore` is committed in each service.
- No file the stack needs is in `.gitignore` (a common one: `infra/` or `*.conf`).
- No absolute paths, no `host.docker.internal`, no names of your user or machine anywhere. Search: `git grep -n "C:\\\\\|/home/\|host.docker.internal"`.

### 13.5 The three commands

```bash
# 1. Start everything and leave it running (the demo)
docker compose up --build -d
docker compose logs smoke                  # ends with: PASS: simulator -> gateway -> broker works end to end

# 2. Or: run once as a test and stop everything again; the exit code is the result
docker compose up --build --abort-on-container-exit --exit-code-from smoke

# 3. Remove everything
docker compose --profile ha down -v --rmi all --remove-orphans
```

What each flag does: `--build` builds our images before starting. `-d` runs in the background. `--abort-on-container-exit` stops all services when the first one exits (the smoke test), and `--exit-code-from smoke` makes the command's exit code the smoke test's. In `down`, `-v` removes the named volumes, `--rmi all` removes every image the stack used (ours and the pulled ones), `--remove-orphans` removes containers of services no longer in the file, and `--profile ha` makes sure the optional Home Assistant service is included even if it was started.
Without the profile, `docker compose down` silently skips services that belong to a profile. This is the usual reason for leftovers.

Build **cache** is kept by Docker separately. A grader who wants even that gone runs `docker builder prune -f`. Put it in the quickstart.

Useful while it runs:

| URL | What |
| --- | --- |
| `http://localhost:8001/docs` | the simulator: `/live`, `/state`, `POST /scenario/{name}` |
| `http://localhost:8002/stats` | the gateway's counters |
| `http://localhost:8123` | Home Assistant (only with `docker compose --profile ha up -d`; first start needs a one-time account and MQTT setup, broker `mosquitto`, port 1883) |

Watch the live MQTT traffic without installing anything on the host:

```bash
docker compose exec mosquitto mosquitto_sub -t "bms/#" -v
```

### 13.6 Rehearse on a machine that is not yours

This is the real test. Do not skip it. Use a teammate's laptop, a fresh virtual machine, or a cloud Linux box with only Docker installed.

1. `git clone` your fork (HTTPS, not your SSH setup), `cd` into it.
2. Run command 2 above. Time it. The first run downloads images and builds. Expect a few minutes.
3. It must end with `PASS` and exit code 0 (`echo $?` or `$LASTEXITCODE`).
4. Run command 1, open the URLs, trigger `curl -X POST localhost:8001/scenario/overheat` and watch the `errors` list in the MQTT traffic.
5. Run command 3. Then check that nothing is left, **by name**:

   ```bash
   docker ps -a --filter "label=com.docker.compose.project=oseas-bms"      # empty
   docker volume ls --filter "label=com.docker.compose.project=oseas-bms"  # empty
   docker network ls --filter "label=com.docker.compose.project=oseas-bms" # empty
   docker images | grep -E "oseas-bms|eclipse-mosquitto"                   # empty
   ```
6. Write down every problem the other person hit. Each one is a missing line in the quickstart.

### 13.7 When it does not work

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `port is already allocated` | another program uses 1883, 8001 or 8002 | copy `.env.example` to `.env`, change the port |
| `docker: 'compose' is not a docker command` | Compose v1 or no plugin | install Docker Desktop, or the Compose v2 plugin |
| `Cannot connect to the Docker daemon` | Docker is not running | start Docker Desktop |
| `mosquitto` unhealthy, config error in logs | CRLF line endings or a missing config file | check `.gitattributes`; re-clone |
| `wifi-sim` waits forever | the simulator is not healthy | `docker compose logs bms-simulator` |
| `smoke` says `FAIL: timed out ... retained messages` | gateway never got a valid line | `curl localhost:8002/stats`: look at `bad_crc` and `no_cells` |
| pull fails or stalls | no internet or a corporate proxy | configure Docker's proxy settings, or pre-pull the images |
| a different CPU (Apple silicon, ARM Linux) | none expected | all images used are multi-architecture |

### 13.8 The quickstart document

Create `docs/quickstart.md` (and link it from the very top of the root `README.md`). Keep it under one screen. Prerequisites in one line, the three commands, what PASS looks like, the URLs, and the troubleshooting table above. A grader reads this first. Everything else is optional for them.

## Your turn

1. Break the stack on purpose and check the smoke test says so: stop the `wifi-sim` container (`docker compose stop wifi-sim`) and rerun only the test (`docker compose run --rm smoke`). What does it print? How long does it take to give up?
2. Change `SPEED` to `10` in `.env` and rerun the test. Which check now fails, and why? What would you change in the smoke test so that it works at any speed? (Hint: look at what the overheat scenario's duration depends on.)
3. Add a sixth check: after `POST /wifi/down` on the gateway, the retained `status` becomes `offline`; after `/wifi/up` it is `online` again.

<details>
<summary>Hints</summary>

1. `timed out after N s waiting for: gateway /health`, after `TIMEOUT_S` (default 120 s). Lower the default if that feels too slow, but not below the time Docker needs to start the services.
2. Check 5 times out, because the overheat takes 1.6 simulated hours, which at speed 10 is nearly ten real minutes. Make the timeout depend on the speed, or have the test read `SPEED` and scale it.
3. Use a fresh late-joiner connection to read the retained status each time. A subscriber connected the whole time sees the live message too, but only the retained one proves what a newcomer would see.

</details>

## Checkpoint

- On a second machine, with only Docker installed: command 2 ends with `PASS` and exit code 0.
- Command 3 followed by the four `docker ... --filter` checks shows nothing left.
- `docs/quickstart.md` exists and a person who has never seen the project can follow it alone.

## Commit

```bash
git add docker-compose.yml .env.example .gitattributes services docs/quickstart.md README.md
git commit -m "feat: one-command stack with end-to-end smoke test"
```
