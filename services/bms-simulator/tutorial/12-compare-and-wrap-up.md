# Part 12: Compare, review and wrap up

**Goal.** Prove that the simulator does what you claim, find its gaps, and leave the team a clear picture of what is real, what is modelled and what is next.

**Time.** 1 hour, plus whatever stretch goal you pick.

## 12.1 Compare with the real firmware

You have three things to compare. Fill in the table honestly. A "no" is a result, not a failure.

| Check | How | Result |
| --- | --- | --- |
| Our firmware layer prints the same line as `native_sim` for the empty chip | `uv run pytest tests/test_golden.py` | |
| Item names, order and decimals of `/live` equal those of `native_sim` | compare a `/live` line with a captured line item by item, ignoring values | |
| Error flag bit positions equal those of the firmware | `ERROR_BITS` against `firmware-findings.md`, section 5 | |
| Our defaults equal `?Conf` in `native_sim` | `Config` fields against your captured `?Conf` | |
| The OCV table equals `sOcvPoints_V` (reversed) | `chemistry.py` against `?Conf` | |
| The gateway's MQTT `meas` equals the simulator's `/live` values (after rounding) | `curl localhost:8001/live`, decode it, and compare with the latest `meas` message | |
| The real `native_sim` stream passes through the gateway without a CRC error | Part 10.8, `bad_crc` is 0 | |

For the sixth check expect tiny differences: the gateway rounds (2 decimals for voltage, 1 for SoC) and polls once a second, so the two values are up to a second apart.

### Production-parity review

Answer each line with *yes*, *no* or *do not know*. Every *no* or *do not know* is a task for the team, or a question for Joscha.

| Question | Answer |
| --- | --- |
| Does every field of the `meas` message come from a ThingSet `Meas` item (plus gateway-supplied time, id, schema)? | |
| Can the gateway run against a real C1 by changing only `SOURCE`? | |
| Does Home Assistant receive its discovery from the gateway, not from the simulator? | |
| Are the fields MPM needs (SoH, cycle count, imbalance, fault log, last-seen) derivable from the stream, and where would that derivation live? | |
| Would an MPM ingestor written against our topics need any change for the real BMS? | |

Questions to ask the case giver (Joscha) before spending time on production-specific work, because they could not be settled from the public documents:

1. How do MPM's plugins receive data today (webhook, polling, MQTT subscription)? Which of those should the MPM ingestor imitate?
2. Is the UART contract for the Cicada relay (path A2) the existing `#mLive` ThingSet statement with CRC, or a new framed format?
3. Which BMS firmware revision will be on the shared real BMS, and is its UART enabled with the default 115200 baud?
4. Does the shared real BMS have cells connected, and how many?
5. Is a retained-message `status` plus last will acceptable for MPM's "last seen", or does MPM need a heartbeat event? (`bms/<id>/mpm` already sends a `heartbeat` every 300 s by default: is that the right interval?)

## 12.2 What is real, modelled or invented

Write this table into `services/bms-simulator/readme.md`. A reviewer must be able to tell, at a glance, which numbers to trust.

| Output | Status | Note |
| --- | --- | --- |
| Line format, item names, decimals, CRC | **Real** (verified byte for byte) | native_sim golden test |
| Error flag bits and names, state numbers | **Real** | from the firmware source |
| Protection limits, hysteresis, balancing rules | **Real defaults** | from `bms_init_config`; delays not modelled |
| SoC estimator (OCV at boot, coulomb counting in 0.1 % steps) | **Real algorithm** | the battery it observes is modelled |
| Cell voltages, current, temperatures | **Modelled** | simplified LFP physics |
| `CELL_FAILURE` rule | **Approximation** | the real chip decides this differently |
| `cell_drift`, `overheat` and `cloudy` scenarios | **Invented** | demonstration stories, not safety tests |
| Cell count on the real stream | **Assumed** | counted from non-zero channels, to be verified on a real pack |

## 12.3 Review your own work

Read your code once as if someone else wrote it, and answer in a notes file.

1. Which function would you be afraid to change? Is there a test that would catch a mistake there? If not, write one.
2. Where does a unit appear without being in the name?
3. What happens if two threads call `step()` at once? (Where is the lock, and is there any other code that touches `battery` or `fw` outside it?)
4. Which part of the system would break first if the broker used a password? (Hint: look at both `publisher.py` and `relay.py`.)
5. Read your commit history with `git log --oneline`. Do the messages tell the story?

## 12.4 Tell the team

- Open a short note or issue: what the simulator does, how to start it (`docker compose up --build`), what is real versus modelled, and what you found. Suggested findings to share:
  the firmware has no MQTT/WiFi, `#mLive` and its CRC, native_sim shows an empty chip, the schema's `cell_voltages_v` minimum of 1 rejects the empty-chip stream (is that what the team wants?), the `info` limits come from assumed defaults when the source is a serial stream.
- Compare against your teammate's branch `feat/mqtt-simulator-ha`: `git diff main..origin/feat/mqtt-simulator-ha --stat`. Which parts can be merged? Which design differs, and which is better? Argue it with evidence (tests, firmware behaviour), not preference.
- Before opening a pull request to the upstream repository, read its `CONTRIBUTING.md` again and disclose meaningful AI assistance as it requires. You wrote this code yourself, but the tutorial and the reference code were written with AI help, and a reviewer should know.

## 12.5 Stretch goals (pick by interest)

1. **Put the real firmware in the loop (hardest, most valuable).** Make `native_sim`'s emulated BQ76952 return values from a file or socket that the battery model fills. This means reading `drivers/bms_ic/bq769x2` in the firmware repository, the `native_sim` emulator that stands in for the chip, and a small C change
   behind a build option. The payoff: the *real* firmware's protections, SoC and state machine process your battery, and the gateway turns its output into MQTT. Work on a fork branch of `bms-firmware`.
2. **Serial over TCP from the simulator.** Add a TCP server to `bms-simulator` that sends a `#mLive` line once per second, to test the gateway's `tcp://` source without `socat`.
3. **ThingSet requests.** Make the simulator answer `?Meas` and `=Conf` text requests on that TCP port, so ThingSet tools can talk to it.
4. **Buffering and clocks** in the gateway (Part 10, exercises 1 and 2).
5. **A shared Python package** for `thingset`, `schema` and `discovery`, installed by both services, replacing the copies.
6. **A broker-backed integration test** that starts Mosquitto in Docker, runs the publisher and asserts what arrives (retained status, LWT on kill, reconnect after a broker restart).
7. **Overcharge and deep-discharge scenarios** for the under-voltage and over-voltage protections.

## What you can now do

- Read an unfamiliar firmware's source, run it, and find out what it really emits.
- Model a physical system and a controller separately, and connect them with a narrow interface.
- Test against a captured ground truth (the golden test) and against behaviour (scenarios).
- Build a robust MQTT publisher (last will, retained state, reconnect) and a gateway that survives dirty input.
- Package and run a multi-service system with Docker Compose.

That is a real piece of an IoT stack, written by you.
