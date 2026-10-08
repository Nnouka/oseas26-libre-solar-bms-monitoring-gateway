# BMS Simulator: Learning Path

A step-by-step path to building the `bms-simulator` service from scratch.
Each stage has things to read, the idea to understand, and a small exercise.
The algorithms are described in words and pseudocode; the code is yours to write.

Link note: if a link is broken, search for the page title.

## Stage 0: Python basics

**Read:** the official Python Tutorial (https://docs.python.org/3/tutorial), sections 3 to 9: numbers and strings, lists and dicts, functions, modules, classes.

**Also learn:** `venv` + `pip`, `dataclasses`, the `random` and `math` modules, `json.dumps`, `time.sleep`.

**Exercise:** write a script that prints a dict as JSON once per second, 5 times.

## Stage 1: The `/health` endpoint

**Read:** the FastAPI tutorial (https://fastapi.tiangolo.com/tutorial), first steps and path operations only.

**Idea:** a web server is a program that waits for requests and returns data.

**Exercise:** a `/health` endpoint that returns `{"status":"ok","service":"bms-simulator"}`. Run it with `uvicorn` and open it in your browser.

## Stage 2: MQTT concepts

**Read:**
- HiveMQ "MQTT Essentials" series (https://www.hivemq.com/mqtt/mqtt-essentials), parts 1 to 5: publish/subscribe, topics and wildcards, QoS, retained messages, last will.
- Home Assistant MQTT docs (https://www.home-assistant.io/integrations/mqtt), discovery section only for now.

**Idea:** a publisher doesn't know who is listening. The broker routes messages by topic.

**Exercise:**
1. Run Mosquitto from your compose file.
2. In two terminals run `mosquitto_sub -t 'test/#' -v` and `mosquitto_pub -t test/hello -m hi`.
3. Try retained messages (`-r`) and note what a new subscriber sees.

## Stage 3: Publish from Python

**Read:** the `paho-mqtt` docs (https://eclipse.dev/paho). paho 2.x changed its callback API, so follow the docs for the version you install.

**Learn:** connect, `loop_start()`, publish, reconnect behaviour, Last Will message.

**Exercise:** a loop that publishes a fake `{"soc": 80}` every second, plus a subscriber in a second terminal that prints it. Then stop the broker and see what your code does. Handling that gracefully is the actual goal.

## Stage 4: The battery model (the interesting part)

Build this as a plain Python class with no MQTT or web code in it.
Give it a `step(dt)` method that advances time and a `snapshot()` method that returns a dict.
Test it by printing values.

**State:** number of cells (for example 8, LiFePO4; check the real board's limits in the `bms-c1` repo), capacity in Ah, SoC per cell, temperature, current, cumulative Ah throughput.

**Algorithms:**

1. **Current profile.** Positive during the day when solar charges, negative in the evening when loads discharge. A smooth bell curve plus small noise is enough. Add a time-acceleration factor so a day passes in minutes.
2. **State of charge (coulomb counting).**
   `soc += current * dt / (capacity_Ah * 3600)`, clamped to 0..1. Do it per cell if cells have slightly different capacities.
3. **Cell voltage.**
   `v_cell = OCV(soc) + current * R_internal`.
   OCV (open-circuit voltage) comes from a lookup table of a few (soc, volts) points with linear interpolation between them. Research the LiFePO4 curve yourself: note how flat it is in the middle and how steep at the ends. Pack voltage is the sum of the cells.
4. **Imbalance.** Give each cell a slightly different capacity and internal resistance so SoC and voltage drift apart. Imbalance = max cell voltage minus min cell voltage.
5. **Passive balancing (stretch).** Near full charge, bleed the highest cells so they stop rising.
6. **Temperature.** First-order model: `T += (heat - cooling) * dt`, where heat is proportional to `current^2 * R` and cooling is proportional to `(T - T_ambient)`. Tune the constants until values look plausible.
7. **SoH and cycles.** Equivalent full cycles = total Ah moved / (2 * capacity). Reduce SoH slowly as cycles increase.
8. **Faults.** Compare values with thresholds (over/under voltage, over temperature, over current). Set a flag, append to a fault log with a timestamp, and optionally stop the current.

**Exercise:** run 24 simulated hours and print a table every simulated 10 minutes. Check:
- Does SoC rise and fall sensibly?
- Does temperature track current?
- Does one cell drift away from the others?

## Stage 5: Design the data contract

This is the actual deliverable, so do it carefully. Write the draft yourself, then get it reviewed before coding against it.

- Decide the topic layout, for example `bms/<device_id>/state` for the telemetry stream, `bms/<device_id>/events` for faults, and an availability topic using the Last Will.
- Write a JSON example by hand first: device ID, timestamp, SoC, SoH, pack voltage, current, cell voltages, max/min temperature, imbalance, fault flags, uptime.
- Decide units, rounding and timestamp format (ISO 8601, UTC).
- Add a version field (for example `"schema": 1`) so the format can change later.
- Decide which fields go to the low-frequency MPM slice and which stay in the full stream (Home Assistant / Prospect).

## Stage 6: Join the pieces

- Wire the model, the publisher and `/health` together. Run the publisher in a background thread, or use FastAPI lifespan hooks, so both work at once.
- Read configuration from environment variables: broker host, device ID, publish interval, speed factor, scenario name.
- Scenarios to add last: "normal day", "cell drifting", "overheat", "broker flaps".

## Stage 7: Docker

Read the Docker "Get started" guide (https://docs.docker.com/get-started) and the Dockerfile best-practices page.
Then follow the compose steps in the project plan: Dockerfile, healthcheck, `depends_on: condition: service_healthy`.

## Order of work and checkpoints

| Stages | Rough time | Checkpoint |
| --- | --- | --- |
| 0 to 3 | 3 to 5 days | Python publishes a message and a terminal subscriber prints it |
| 4 | about 1 week | The 24-hour table looks believable |
| 5 | 1 to 2 days + review | Schema draft agreed |
| 6 and 7 | a few days | `docker compose up` shows the service healthy and publishing |

## Tips for learning

- Write tiny scripts and run them often. Don't write 200 lines before running anything.
- Read error messages from the bottom up.
- When stuck, note what you expected, what happened, and what you tried.
- Keep model code separate from I/O code so it can be reused for the other simulated pieces.
