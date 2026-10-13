# Build the BMS simulator yourself: a step-by-step tutorial

By the end of this tutorial you will have written, understood and tested a Python program that behaves like a
**Libre Solar BMS C1 running the real firmware**, and that feeds this project's MQTT gateway. You type
all of the code. The tutorial gives you the goal, the ideas, the tests that define "correct", hints, and a
hidden solution to compare against when you are stuck.

## Assumption: it will run on a machine that is not yours

Graders will clone the repository and run **one command to start and one to remove everything**, with only Docker installed. [Part 13](13-one-command.md) makes that true and proves it. Keep it in mind from the start: no absolute paths, no
host-only tools, no files outside the repository.

## The rule that shapes everything: production parity

Only build what the production system can use **without major changes**. Concretely:

1. **One MQTT publisher: the gateway.** The simulator pretends to be the *BMS* (it produces the same serial `#mLive` line as the firmware). The gateway turns that line into MQTT, and it is the only component that does.
   Whatever sits behind the gateway (the simulator, `native_sim`, a real C1 on a USB-serial adapter) is chosen by one setting, `SOURCE`. Home Assistant, MPM and Prospect cannot tell the difference, and nothing else changes when the real board arrives.
2. **Nothing in the MQTT contract that the firmware cannot supply.** Every field in `meas` comes from a ThingSet `Meas` item. The gateway adds only what a gateway can know: the time, the device id, the schema version.
   Simulator-only knowledge (the true charge, the scenarios) is never put on MQTT.
3. **Home Assistant discovery lives in the gateway**, not in the device, so it works for any source.
4. **Fields consumers want but the firmware does not have** (state of health, cycle count, imbalance, health score) are derived downstream (in the gateway or the MPM ingestor) from the stream. They are never invented by the simulator.

5. **Two topics, two audiences.** `bms/<id>/meas` is the full measurement at a high rate, for Home Assistant. `bms/<id>/mpm` is a compact summary published only when something is worth a decision (state or fault change, imbalance across its limit, a real SoC move) plus a slow heartbeat, for MPM. Same device, same gateway, two subscribers who each get what they need ([Part 7.6](07-publisher.md)).

The part of the tutorial where the simulator publishes MQTT directly (Parts 7 and 8) teaches the publisher and discovery code that the gateway then reuses (Part 10). In the final stack the simulator runs with `MQTT_ENABLED=false`.

## What you are building, and why it looks the way it does

The hackathon needs a stand-in for a real battery so that the broker, Home Assistant, Prospect and the MPM
ingestor can be developed without hardware. In [Part 0](00-native-sim.md) you will run the **real BMS firmware**
on your laptop (Zephyr `native_sim`) and see exactly what it says. Two findings drive the design (details in
[firmware-findings.md](../../../docs/firmware-findings.md)):

1. The real firmware **does not speak MQTT**. It prints a ThingSet line (`#mLive {...} CRC#`) on a serial port once
   a second. So our simulator makes that same line first, and then translates it to MQTT, the way a real gateway
   would have to.
2. The real BMS reports **what the firmware believes**, not the truth. Its State of Charge is an estimate that is
   initialised from a voltage table and then counted with a current integral; it can be wrong. So the simulator
   keeps the **physical battery** and the **firmware** apart.

```text
 physical_battery.py            chip.py                firmware.py             thingset.py
 the physics     ->    ChipReading      ->    what the BMS       ->   "#mLive {..} CRC#"
 (truth)               (what the chip         believes and does       (the serial line)
      ^                 measures)                  |    |                    |
      |                                            |    +-- switches --------+        mapping.py
      +---------------- MOSFET switches -----------+                                     |
                                                                                          v
                                                         schema.py + publisher.py  ->  MQTT  ->  Home Assistant, ...

 The same serial line can also take the "WiFi" route (Part 10):

 native_sim (real firmware) --#mLive-->  \
                                          wifi-sim (check CRC, map, validate) --MQTT over the network--> broker
 bms-simulator  --/live-->               /
```

## The parts

| Part | You will | You will have | Time |
| --- | --- | --- | --- |
| [0. Run the real firmware](00-native-sim.md) | build and run `native_sim`, capture its `#mLive` line | the ground truth, and a captured line | 1.5-3 h |
| [1. Project setup](01-project-setup.md) | set up uv, tests, branch | a green first test | 45 min |
| [2. MQTT first contact](02-mqtt-first-contact.md) | run a broker; publish, subscribe, retained, last will | feel for the protocol | 1.5 h |
| [3. The ThingSet line](03-thingset-line.md) | encode, decode and checksum `#mLive` | `thingset.py` | 1.5 h |
| [4. The physical battery](04-battery-physics.md) | model cells, current, heat | `chemistry.py`, `chip.py`, `physical_battery.py` | 3-4 h |
| [5. The firmware layer](05-firmware-layer.md) | protections, SoC estimate, balancing, state machine | `firmware.py` and the **golden test** against native_sim | 3-4 h |
| [6. Glue and mapping](06-glue-and-mapping.md) | connect it all and translate to the team's schema | `mapping.py`, `schema.py`, `sim.py` | 1.5 h |
| [7. The MQTT publisher](07-publisher.md) | publish with last will, retained topics, reconnect | `publisher.py` | 2 h |
| [8. Home Assistant](08-home-assistant.md) | announce entities by MQTT discovery | `discovery.py`, entities in HA | 1.5 h |
| [9. API and scenarios](09-api-and-scenarios.md) | FastAPI lifespan, `/health`, `/live`, scenarios | `main.py` | 1.5 h |
| [10. The WiFi gateway](10-wifi-gateway.md) | read the serial `#mLive` stream (simulator or real native_sim), check it, send it over the network | `services/wifi-sim` | 2-3 h |
| [11. Docker Compose](11-docker-compose.md) | containerise and wire the stack | `docker compose up` | 2 h |
| [12. Compare and wrap up](12-compare-and-wrap-up.md) | check against native_sim, review, stretch goals | a finished milestone | 1 h |
| [13. One command up, one command down](13-one-command.md) | final compose, end-to-end smoke test, clean teardown, rehearsal on another machine | what graders will run | 2 h |

About 30 hours in total, which fits comfortably into the four preparation weeks. Parts 0 to 3 are
largely independent of each other; do Part 0 first anyway, because everything after it refers to what you saw there.

## How to work through a part

1. **Read "Goal" and "Ideas".** Ideas are short. Skim the linked reading only if something is unclear.
2. **Type the tests first.** Each part gives you a test file. It is the exact definition of "done". Run it and watch it fail.
3. **Write the code yourself.** The skeleton and the hints tell you what each function must do. The first try
   does not have to be pretty.
4. **Run the tests, then the checkpoint script**, and compare your output with the expected output shown. Numbers
   will differ slightly if you draw random numbers in a different order. Shapes and ranges must match.
5. **Only then open the solution** (`<details>`), compare with yours, and note the differences. Do not copy before you try.
6. **Commit** with a message like the one suggested.

When something fails, read the *whole* error message from the bottom up before changing anything. That habit is
worth more than any single technique here.

## Conventions

- Work in `services/bms-simulator/`. All commands are run from there unless a part says otherwise.
- Commands work in PowerShell and in bash unless noted. `uv run X` runs `X` in the project's virtual environment.
- Python 3.12. The code uses type hints (`list[float]`, `float | None`) so your editor can help you.
- Units are in every name: `_v` volts, `_a` amps, `_c` degrees Celsius, `_s` seconds, `_ms` milliseconds.
  **Current is positive when the battery is charging.**
- Test counts like "4 passed" in the checkpoints refer to the test file for that part.

## Working with your team

Your teammate has a version of this simulator on the branch `feat/mqtt-simulator-ha` (`docs/mqtt-schema.md`). The
MQTT messages built here follow that contract, so the two are interchangeable for consumers. Build yours on a branch of your
own, for example `learn/bms-simulator`, and agree with the team before opening a pull request to the upstream
repository. Keep the AI-use disclosure that the upstream `CONTRIBUTING.md` asks for in any pull request.

## Not covered

Battery safety certification, a real BQ76952 register-level model, ThingSet over CAN, and anything about the
cloud side (Prospect, MPM). The simulator is a software stand-in. It never proves that real protection hardware works.
