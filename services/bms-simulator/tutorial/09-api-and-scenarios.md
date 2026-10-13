# Part 9: API and scenarios

**Goal.** Turn the pieces into a service: a FastAPI app that starts the publisher at startup, stops it cleanly at shutdown, answers `/health`, shows the live `#mLive` line and the internal state, and
lets you trigger scenarios (`cell_drift`, `overheat`, `cloudy`) while it runs. This is `main.py`.

**Time.** 1.5 hours.

## Ideas

**Lifespan.** A web app needs to start and stop background work. FastAPI's `lifespan` is an `async` context manager. The code before `yield` runs once at startup and the code after it once at shutdown, which is where `publisher.start()` and `publisher.stop()` belong. Never start background work inside a request handler.

**Configuration from the environment.** Containers are configured with environment variables, not code edits. `os.getenv("MQTT_HOST", "localhost")` reads one with a default. These are the team's names:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MQTT_HOST` | `localhost` | broker host (in Compose: `mosquitto`) |
| `MQTT_PORT` | `1883` | broker port |
| `DEVICE_ID` | `haske-bms-001` | used in every topic |
| `PUBLISH_INTERVAL_S` | `5` | real seconds between `meas` messages |
| `SPEED` | `60` | simulated seconds per real second |
| `SCENARIO` | `normal` | `normal`, `cell_drift`, `overheat` or `cloudy` |
| `MQTT_ENABLED` | `true` | `false`: no MQTT at all. The simulator behaves like the BMS alone (serial line on `/live`) and the gateway of Part 10 does the publishing |

**Two modes.** With `MQTT_ENABLED=true` (the default while you learn) the `Publisher` of Part 7 both steps the battery and publishes. With `MQTT_ENABLED=false` a tiny `SimRunner` only steps the battery on the same timer, and the simulator is purely "the BMS". Production parity (see the README): in the final stack the gateway is the only publisher.

**Honest health.** `/health` answers "is this process alive and serving?". It says `ok` even if the broker is down, and reports the MQTT connection as a separate field. Docker's healthcheck must not restart the simulator just because the broker is
being restarted.

**`/live`.** Returns the exact line a real BMS would send on its serial port. Anything that understands ThingSet (including the parser you wrote in Part 3) can read it. It is what the WiFi gateway of Part 10 reads.

**`/state`.** Shows what the BMS reports *and* the hidden truth (`true_soc_pct`, `soc_estimate_error_pct`). A debugging window, not a product feature.

**Scenarios.** `POST /scenario/overheat` changes the battery's behaviour while it runs. The firmware layer reacts by itself. This is the demonstration tool for the hackathon: "watch what happens to the Home Assistant dashboard when the battery overheats".

## Steps

### 9.1 Tests first

Move your Part 1 test into a fuller file: `git mv tests/test_health.py tests/test_api.py`, then replace its contents with

```python
from fastapi.testclient import TestClient

import main


def test_health_is_ok_even_without_a_broker():
    # no `with` block => the lifespan (and so the MQTT thread) is not started
    client = TestClient(main.app)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "bms-simulator"
    assert body["mqtt_connected"] is False


def test_live_endpoint_returns_a_valid_line():
    from thingset import decode_live
    client = TestClient(main.app)
    assert "rSOC_pct" in decode_live(client.get("/live").text)


def test_unknown_scenario_returns_400():
    client = TestClient(main.app)
    assert client.post("/scenario/bogus").status_code == 400
    assert client.post("/scenario/overheat").json() == {"scenario": "overheat"}
    client.post("/scenario/normal")


def test_without_mqtt_the_simulator_still_runs_and_serves_the_live_line(monkeypatch):
    import importlib

    monkeypatch.setenv("MQTT_ENABLED", "false")
    nomqtt = importlib.reload(main)
    try:
        with TestClient(nomqtt.app) as client:           # the lifespan runs: the SimRunner starts
            body = client.get("/health").json()
            assert body["mqtt_enabled"] is False and body["mqtt_connected"] is None
            assert client.get("/live").text.startswith("#mLive")
    finally:
        monkeypatch.delenv("MQTT_ENABLED")
        importlib.reload(main)
```

The comment in the first test is important: *without* a `with TestClient(...)` block the lifespan does not run, so the tests do not start an MQTT thread or need a broker.

### 9.2 `main.py`

Write it in this order:

1. imports and `logging.basicConfig`,
2. read the six environment variables (convert the numbers: `int(...)`, `float(...)`),
3. create `model = Simulator(scenario=SCENARIO)`, `publisher = Publisher(model, ...)` **only if** `MQTT_ENABLED` (else `None`), and a `SimRunner` (new file `runner.py`, below),
4. the `lifespan` function with `@asynccontextmanager`; it starts and stops `publisher or runner`,
5. `app = FastAPI(title="BMS simulator", lifespan=lifespan)`,
6. endpoints:
   - `GET /` returns a small JSON with a pointer to `/docs`,
   - `GET /health` returns `status`, `service`, `mqtt_enabled` and `mqtt_connected` (`None` when MQTT is disabled),
   - `GET /state` returns the device id, the number of published messages, `model.debug()` and `model.snapshot()` merged into one dict,
   - `GET /live` returns `model.live_line()` as **plain text** (`response_class=PlainTextResponse`),
   - `POST /scenario/{name}` calls `model.set_scenario(name)` and answers `{"scenario": name}`, or **HTTP 400** with a helpful message for an unknown name (catch the `ValueError`, raise `HTTPException(status_code=400, ...)`).

<details>
<summary>Solution: runner.py</summary>

```python
"""Steps the simulator on a timer when it does NOT publish MQTT itself (MQTT_ENABLED=false)."""
from __future__ import annotations

import threading

from sim import Simulator


class SimRunner:
    def __init__(self, model: Simulator, interval_s: float, speed: float):
        self.model, self.interval_s, self.speed = model, interval_s, speed
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="sim-runner", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.model.step(self.interval_s * self.speed)
            self._stop.wait(self.interval_s)
```

The same loop as the publisher's, without the network.

</details>

<details>
<summary>Solution: main.py</summary>

```python
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from battery import SCENARIOS
from sim import Simulator
from publisher import Publisher
from runner import SimRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DEVICE_ID = os.getenv("DEVICE_ID", "haske-bms-001")
INTERVAL_S = float(os.getenv("PUBLISH_INTERVAL_S", "5"))
SPEED = float(os.getenv("SPEED", "60"))
SCENARIO = os.getenv("SCENARIO", "normal")
# false: the simulator behaves like the BMS alone (serial line on /live) and the WiFi gateway publishes MQTT
MQTT_ENABLED = os.getenv("MQTT_ENABLED", "true").lower() in ("1", "true", "yes")

model = Simulator(scenario=SCENARIO)
publisher = Publisher(model, MQTT_HOST, MQTT_PORT, DEVICE_ID, INTERVAL_S, SPEED) if MQTT_ENABLED else None
runner = SimRunner(model, INTERVAL_S, SPEED)


@asynccontextmanager
async def lifespan(app: FastAPI):
    (publisher or runner).start()      # runs once at startup
    yield                              # the app serves requests while we are suspended here
    (publisher or runner).stop()       # runs once at shutdown


app = FastAPI(title="BMS simulator", lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "BMS simulator", "docs": "/docs"}


@app.get("/health")
async def health():
    # "ok" even when the broker is down: the simulator itself is alive.
    return {"status": "ok", "service": "bms-simulator", "mqtt_enabled": MQTT_ENABLED,
            "mqtt_connected": publisher.connected if publisher else None}


@app.get("/state")
async def state():
    return {"device_id": DEVICE_ID, "published": publisher.published if publisher else 0,
            **model.debug(), **model.snapshot()}


@app.get("/live", response_class=PlainTextResponse)
async def live():
    """The line the real firmware sends on its serial port (ThingSet #mLive statement)."""
    return model.live_line()


@app.post("/scenario/{name}")
async def set_scenario(name: str):
    try:
        model.set_scenario(name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown scenario; choose from {list(SCENARIOS)}")
    return {"scenario": name}
```

</details>

Run the tests: `uv run pytest tests/test_api.py -q` should say `4 passed`. Then the whole suite: `uv run pytest -q`, `64 passed`. The last API test reloads the app with `MQTT_ENABLED=false` and checks that the simulator still runs and serves `/live`.

### 9.3 Run the service

With the broker running:

```bash
uv run uvicorn main:app --port 8000
```

PowerShell sets environment variables like this (it lasts for that terminal):

```powershell
$env:SCENARIO = "cell_drift"; $env:SPEED = "600"; $env:PUBLISH_INTERVAL_S = "1"; uv run uvicorn main:app --port 8000
```

bash: `SCENARIO=cell_drift SPEED=600 PUBLISH_INTERVAL_S=1 uv run uvicorn main:app --port 8000`.

Open <http://localhost:8000/docs>. FastAPI generated an interactive page for every endpoint, so you can try them with a click. Look at:

- `/health` which should report `"mqtt_connected": true` once the broker is up,
- `/live` which is a `#mLive` line like the one from native_sim, but with real-looking numbers,
- `/state` which compares the BMS's belief with the truth.

### 9.4 Watch the scenarios

Run the scenario script directly (it needs no server) to see the whole story at a glance:

```python
"""Part 9: run each scenario at 1-minute steps and print every change of state/flags."""
from sim import Simulator

for scenario in ("cell_drift", "overheat"):
    sim = Simulator(seed=1, scenario=scenario)
    previous = None
    for minute in range(1, 24 * 60 + 1):
        sim.step(60)
        snap = sim.snapshot()
        key = (snap["state"], tuple(snap["errors"]))
        if key != previous:
            v = snap["cell_voltages_v"]
            print(f"{scenario:10} +{minute / 60:5.2f} h  {snap['state']:7} {snap['errors'] or '-'}"
                  f"  spread={1000 * (max(v) - min(v)):4.0f} mV  Tmax={max(snap['cell_temps_c']):.1f}")
            previous = key
```

Save it as `dev_scenarios.py` and run `uv run python dev_scenarios.py`. Reference result:

```text
cell_drift + 0.02 h  NORMAL  -  spread=   3 mV  Tmax=29.0
cell_drift +14.93 h  CHG     ['CELL_FAILURE']  spread= 403 mV  Tmax=28.3
cell_drift +14.95 h  OFF     ['CELL_FAILURE']  spread= 398 mV  Tmax=28.2
cell_drift +15.60 h  OFF     ['CELL_UNDERVOLTAGE', 'CELL_FAILURE']  spread= 775 mV  Tmax=28.1
overheat   + 0.02 h  NORMAL  -  spread=   3 mV  Tmax=29.0
overheat   + 1.58 h  OFF     ['DIS_OVERTEMP', 'CHG_OVERTEMP']  spread=   2 mV  Tmax=45.4
```

Read the output as a story.

- **`cell_drift`**: cell 2 leaks and its resistance grows. After about fifteen simulated hours the spread between the cells passes 400 mV, `CELL_FAILURE` is raised, the BMS goes `CHG` then `OFF`, and a little later the leaking cell hits
  `CELL_UNDERVOLTAGE`. Both switches are open and nothing closes them again. The leaked cell stays empty even if you switch back to `normal`, so restart the service to start over (`POST /scenario/normal` only stops *further* damage).
- **`overheat`**: the surroundings warm up by 0.25 °C per minute. The pack follows, with a delay (thermal mass). At 45 °C **both** over-temperature flags trip at once, because the firmware's defaults give charge and discharge the same limit,
  and the state goes straight to `OFF`.

Now do it live. Start the service with `SPEED=600` and `PUBLISH_INTERVAL_S=1` (one simulated ten minutes per second), watch Home Assistant, and trigger:

```bash
curl -X POST localhost:8000/scenario/overheat
```

(PowerShell: `Invoke-RestMethod -Method Post http://localhost:8000/scenario/overheat`.) The temperature chart climbs and the *Fault* indicator turns on after about ten real seconds. Then restore:

```bash
curl -X POST localhost:8000/scenario/normal
```

The ambient cools over about thirty simulated minutes, the flags clear only when the temperature falls below the limit **minus the 5 degree hysteresis**, and the BMS goes back through `CHG` or `DIS` to `NORMAL`.

## Your turn

1. Add a scenario `deep_discharge`: a load so large that the pack empties and the under-voltage protection fires. Which two places need to change? (Hint: `SCENARIOS` and `_apply_scenario`/`wanted_current` in `battery.py`. Write the scenario test in `test_sim.py` first.)
2. Add `GET /scenarios` returning the list of known scenarios. Write the test first.
3. Make `POST /scenario/{name}` reject unknown names with the list of valid ones in the error message. (It already does. Where does the list come from? Why is it good that there is only one `SCENARIOS` tuple?)

## Checkpoint

- `uv run pytest -q` shows `64 passed`.
- The service runs, `/health` is ok, and you can trigger `overheat` and see the effect in `mosquitto_sub` (the `errors` list fills) and, if you ran Part 8, in Home Assistant.

## Commit

```bash
git add services/bms-simulator
git commit -m "feat(bms-simulator): FastAPI app with health, live line, state and scenarios"
```

Next: [Part 10: The WiFi gateway simulator](10-wifi-gateway.md).
