# MPM without SMTP, and More Scenarios: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop MicroPowerManager (MPM) company registration failing for lack of an SMTP server (TODO item 4), and add the missing battery scenarios to the firmware rig and the pure-Python simulator (TODO item 1).

**Architecture:** Item 4 makes `integrations/micropowermanager/apply.py` write `MAIL_MAILER=log` into MPM's optional compose env-override file. Item 1 replaces the `if` chain in `Battery._apply_scenario` with one small handler per scenario, plus a "forced current" hook for the scenarios that dictate the current. `services/firmware-rig/battery.py` and `services/bms-simulator/battery.py` are byte-identical today and stay identical.

**Tech Stack:** Python 3.12, pytest, uv, FastAPI (existing); Docker Compose; MPM (Laravel) env files.

**Spec:** `docs/superpowers/specs/2026-10-15-high-priority-todos-design.md` (sections "Item 4" and "Item 1"). Items 2 and 3 get their own plans later.

## Global Constraints

- Order: item 4 first (Tasks 1-2), then item 1 (Tasks 3-6).
- `services/firmware-rig/battery.py` and `services/bms-simulator/battery.py` must stay identical (`diff` prints nothing).
- `SCENARIOS` in `battery.py` is the single source of truth; `POST /scenario/{name}` validates against it (no change to `api.py`).
- Existing scenarios `normal`, `cell_drift`, `overheat`, `cloudy` keep their exact behaviour; their existing tests must still pass unchanged.
- Chip protection limits are the ones in `services/firmware-rig/protections.py` (OCD1 trips when discharge current exceeds the capacity in amps; CUV below 2.50 V). Do not change them.
- No new Python dependencies. No changes to `api.py`, the bridge, or MPM source (only the env override file).
- Commit messages end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Run rig tests from `services/firmware-rig` with `uv run pytest tests -q`; simulator tests from `services/bms-simulator` with `uv run pytest tests -q`.

## Review Focus

Inputs the spec implies but a happy-path test would miss, most likely first. Each has a test in the task named:

1. `apply.py` run twice, or against an override file the user already edited (other variables, CRLF line endings, an existing `MAIL_MAILER=smtp`): the user's other lines must survive and `MAIL_MAILER` ends up `log` exactly once. (Task 2)
2. Switching away from a forced-current scenario (`charging`, `overcurrent`, ...) back to `normal` must restore the normal solar/load profile, not leave the current stuck. (Task 3)
3. `undervoltage` on a small pack (`cells=2` or `4`): the leaking cell index must exist. (Task 4)
4. `instant_recovery` must return to `normal` on its own and the protection must clear afterwards. (Task 4)
5. `low_soc` selected repeatedly (or after `charging`) must reset the state of charge each time and must not keep a stale forced current when left. (Task 3)
6. Unknown names still return 400 and the error lists the new names. (Task 5)

---

## PART A: Item 4, local MPM without SMTP

### Task 1: Reproduce the registration failure and confirm the cause

This is a diagnosis step; nothing is committed. The spec assumes SMTP is the cause. **If this task proves otherwise, stop and report back; do not do Task 2.**

**Files:** none modified.

**Interfaces:**
- Consumes: a running MPM from `integrations/micropowermanager/README.md` steps 1-4 (checkout at `C:\hackathons\en-access\micropowermanager`).
- Produces: a yes/no on "MPM registration fails because the welcome mail is sent over an unreachable SMTP server", plus the exact log line.

Background (already read): registration fires `UserCreatedEvent`; `App\Listeners\UserListener::handle` calls `MailHelper::sendViaTemplate`, which does `Mail::to(...)->queue(...)`. When `QUEUE_CONNECTION` is `sync` (Laravel's default when unset), `queue()` sends inline, so an unreachable SMTP host (`MAIL_HOST="smtp.mailserver"` in `src/backend/.env.example`) throws a Symfony transport exception inside the request.

- [ ] **Step 1: Check the queue and mail settings the dev containers actually use**

Run (from `C:\hackathons\en-access\micropowermanager`):
```bash
grep -n -i "QUEUE_CONNECTION\|MAIL_" dev/.env.micropowermanager-backend; echo "exit=$?"
```
Expected: no `QUEUE_CONNECTION` line (so Laravel defaults to `sync`) and no `MAIL_MAILER` line (default `smtp`). Write down what you see.

- [ ] **Step 2: Reproduce**

With MPM up (`docker compose up -d` in the MPM folder), open http://localhost:8001, register a new company with a valid-looking email (for example `owner@example.com`). Expected: registration shows an error.

- [ ] **Step 3: Find the error in the backend log**

```bash
docker logs backend-dev 2>&1 | grep -i -E "transport|smtp|mailserver|MailNotSent|Failed to send" | tail -20
```
Expected: a line such as `Failed to send template email to owner@example.com: Connection could not be established with host "smtp.mailserver..."`, or a `TransportException` stack frame from `MailHelper`.

- [ ] **Step 4: Decide**

If the log shows the SMTP transport error at the moment of registration: continue to Task 2. If it shows something else (database, plugin, tenant migration), stop and report the exact error to the user.

---

### Task 2: `apply.py` writes `MAIL_MAILER=log`

**Files:**
- Modify: `integrations/micropowermanager/apply.py` (add `MAIL_OVERRIDE`, `disable_mail`, and one call in `main`)
- Create: `integrations/micropowermanager/tests/conftest.py`
- Create: `integrations/micropowermanager/tests/test_apply_mail.py`
- Modify: `integrations/micropowermanager/README.md` (step 4 and Troubleshooting)

**Interfaces:**
- Produces: `apply.disable_mail(mpm: Path) -> str`. Writes or updates `<mpm>/.env.override.micropowermanager-backend` so it contains exactly one `MAIL_MAILER=log` line and keeps every other line; returns a one-line status message for printing. Idempotent.
- Consumes: nothing from earlier tasks.

MPM's `docker-compose.yml` lists `.env.override.micropowermanager-backend` (optional, `required: false`) after `dev/.env.micropowermanager-backend` for `backend-dev`, `scheduler-dev` and `queue-worker-dev`; later `env_file` entries override earlier ones, so this file wins.

- [ ] **Step 1: Write the failing tests**

Create `integrations/micropowermanager/tests/conftest.py`:
```python
import sys
from pathlib import Path

# Make `import apply` work when pytest is started from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

Create `integrations/micropowermanager/tests/test_apply_mail.py`:
```python
from apply import MAIL_OVERRIDE, disable_mail


def lines(mpm):
    return (mpm / MAIL_OVERRIDE).read_text(encoding="utf-8").splitlines()


def test_creates_the_override_file_when_missing(tmp_path):
    disable_mail(tmp_path)
    assert lines(tmp_path) == ["MAIL_MAILER=log"]


def test_running_twice_does_not_duplicate_the_line(tmp_path):
    disable_mail(tmp_path)
    assert "already" in disable_mail(tmp_path)
    assert lines(tmp_path).count("MAIL_MAILER=log") == 1


def test_keeps_the_users_other_variables(tmp_path):
    (tmp_path / MAIL_OVERRIDE).write_text("APP_DEBUG=true\nFOO=bar\n", encoding="utf-8")
    disable_mail(tmp_path)
    assert lines(tmp_path) == ["APP_DEBUG=true", "FOO=bar", "MAIL_MAILER=log"]


def test_replaces_an_existing_smtp_mailer_and_handles_crlf(tmp_path):
    (tmp_path / MAIL_OVERRIDE).write_bytes(b"MAIL_MAILER=smtp\r\nFOO=bar\r\n")
    disable_mail(tmp_path)
    assert lines(tmp_path) == ["FOO=bar", "MAIL_MAILER=log"]
    assert b"\r" not in (tmp_path / MAIL_OVERRIDE).read_bytes()      # compose + LF: no stray carriage returns
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (repo root): `uv run --no-project --with pytest python -m pytest integrations/micropowermanager/tests -q`
Expected: FAIL / collection error with `ImportError: cannot import name 'MAIL_OVERRIDE' from 'apply'`.

- [ ] **Step 3: Implement**

In `integrations/micropowermanager/apply.py`, after the `PLUGIN_ID = 33` line add:
```python
MAIL_OVERRIDE = ".env.override.micropowermanager-backend"
```
and, above `def main():`, add:
```python
def disable_mail(mpm: Path) -> str:
    """Make MPM log its mails instead of sending them, so registration works without an SMTP server.

    MPM's compose file reads this optional file after dev/.env.micropowermanager-backend for the
    backend, queue worker and scheduler. Other lines the user put there are kept.
    """
    path = mpm / MAIL_OVERRIDE
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if existing.count("MAIL_MAILER=log") == 1 and not any(
            line.startswith("MAIL_MAILER=") and line != "MAIL_MAILER=log" for line in existing):
        return f"already set: {MAIL_OVERRIDE}"
    kept = [line for line in existing if not line.startswith("MAIL_MAILER=")]
    path.write_text("\n".join(kept + ["MAIL_MAILER=log"]) + "\n", encoding="utf-8", newline="\n")
    return f"set MAIL_MAILER=log in {MAIL_OVERRIDE}"
```
In `main()`, directly before the final `print("\nNext: ...")` add:
```python
    print(disable_mail(mpm))
```
and change that final message to:
```python
    print("\nNext: php artisan migrate && php artisan migrate-tenant")
    print("If MPM is already running, recreate its backend containers so they pick up the mail setting:")
    print("  docker compose up -d --force-recreate backend-dev queue-worker-dev scheduler-dev")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --no-project --with pytest python -m pytest integrations/micropowermanager/tests -q`
Expected: `4 passed`.

- [ ] **Step 5: Update the integration README**

In `integrations/micropowermanager/README.md`:
- In "### 2. Apply our plugin", after the `fix_composer_dockerfiles.py` code block add:
  `` `apply.py` also writes `MAIL_MAILER=log` into `.env.override.micropowermanager-backend` in the MPM folder, so MPM logs its mails instead of sending them. Company registration therefore works without an SMTP server; the welcome mail ends up in the backend log. To use real SMTP, delete that line and set your own `MAIL_*` values there. ``
- In "## Troubleshooting" add the bullet:
  `` - **Company registration fails with a mail or SMTP error**: re-run `apply.py` (it sets `MAIL_MAILER=log`), then recreate the backend containers: `docker compose up -d --force-recreate backend-dev queue-worker-dev scheduler-dev`. ``

- [ ] **Step 6: Verify against a real MPM (manual, from Task 1's setup)**

```bash
python integrations/micropowermanager/apply.py "C:\hackathons\en-access\micropowermanager"
```
Expected output includes `set MAIL_MAILER=log in .env.override.micropowermanager-backend`. Then in the MPM folder: `docker compose up -d --force-recreate backend-dev queue-worker-dev scheduler-dev`, wait until healthy, and register a company at http://localhost:8001. Expected: registration succeeds; `docker logs backend-dev 2>&1 | grep -i "Welcome to MicroPowerManager"` shows the logged mail or the log channel file has it. Report the result.

- [ ] **Step 7: Commit**

```bash
git add integrations/micropowermanager/apply.py integrations/micropowermanager/tests integrations/micropowermanager/README.md
git commit -m "feat(mpm): log mails instead of sending so registration works without SMTP" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## PART B: Item 1, more scenarios

New scenarios (physics only, in `battery.py`):

| Name | What the battery does | Expected visible effect |
|------|-----------------------|-------------------------|
| `charging` | forces +0.2 C (10 A at 50 Ah) around the clock | state CHG, SoC rises |
| `discharging` | forces -0.2 C | state DCH, SoC falls |
| `low_soc` | cells start at 10-14 % and drain slowly at -0.01 C | health amber (SoC < 15 %) |
| `overcurrent` | forces -1.2 C (60 A at 50 Ah) | chip OCD1; firmware over-current fault, FET opens |
| `undervoltage` | one cell leaks 30 A until it collapses | chip CUV; firmware cell under-voltage |
| `instant_recovery` | 600 simulated seconds of the overcurrent surge, then switches itself to `normal` | fault raised then cleared with no manual reset |

### Task 3: Forced-current scenarios (`charging`, `discharging`, `low_soc`, `overcurrent`) and the handler refactor

**Files:**
- Modify: `services/firmware-rig/battery.py`
- Create: `services/firmware-rig/tests/test_battery_scenarios.py`

**Interfaces:**
- Produces (used by Task 4): `Battery.force_c: float | None` (C-rate that overrides the solar/load profile, `None` = normal profile); module constant `FORCED_C_RATE: dict[str, float]`; constants `CHARGING_C`, `DISCHARGING_C`, `LOW_SOC_RANGE`, `LOW_SOC_DRAIN_C`, `OVERCURRENT_C`; scenario handlers are methods named `_scn_<name>(self, dt: float) -> None`, discovered by `_apply_scenario`.
- `SCENARIOS` becomes `("normal", "cell_drift", "overheat", "cloudy", "charging", "discharging", "low_soc", "overcurrent", "undervoltage", "instant_recovery")`. The last two are added to the tuple here so there is one edit of the tuple; their behaviour arrives in Task 4 (until then they behave like `normal`).

- [ ] **Step 1: Write the failing tests**

Create `services/firmware-rig/tests/test_battery_scenarios.py`:
```python
"""The new scenarios, on the physical battery alone (no firmware)."""
from battery import SCENARIOS, Battery
from chip import ChipReading
from protections import ChipProtections

OCD1 = 1 << 5          # SAFETY_STATUS_A bit, see test_protections.py


def step(b: Battery, n: int = 1, dt: float = 60.0) -> None:
    for _ in range(n):
        b.step(dt, True, True, 0)


def test_all_scenario_names_are_registered():
    assert SCENARIOS == ("normal", "cell_drift", "overheat", "cloudy", "charging", "discharging", "low_soc",
                         "overcurrent", "undervoltage", "instant_recovery")


def test_charging_forces_a_charging_current_at_any_hour():
    for hour in (2.0, 12.0, 22.0):
        b = Battery(seed=1, start_hour=hour)
        b.set_scenario("charging")
        step(b)
        assert 5.0 < b.current_a < 15.0, hour


def test_discharging_forces_a_discharge_current_at_any_hour():
    for hour in (2.0, 12.0, 22.0):
        b = Battery(seed=1, start_hour=hour)
        b.set_scenario("discharging")
        step(b)
        assert -15.0 < b.current_a < -5.0, hour


def test_low_soc_starts_the_pack_nearly_empty_and_keeps_it_there():
    b = Battery(seed=1, start_hour=12.0)                     # midday sun would normally charge it
    b.set_scenario("low_soc")
    assert all(0.10 <= s <= 0.14 for s in b.soc)
    step(b, 60)
    assert max(b.soc) < 0.15


def test_low_soc_resets_the_charge_every_time_it_is_selected():
    b = Battery(seed=1)
    b.set_scenario("charging")
    step(b, 120)
    b.set_scenario("low_soc")
    assert max(b.soc) <= 0.14


def test_overcurrent_exceeds_one_c_and_trips_the_discharge_protection():
    b = Battery(seed=1)
    b.set_scenario("overcurrent")
    step(b)
    assert -b.current_a > b.nominal_ah                       # more than 1 C
    status_a, _b, _c = ChipProtections(capacity_ah=b.nominal_ah).update(b.read_chip())
    assert status_a & OCD1


def test_overcurrent_stops_when_the_bms_opens_the_discharge_fet():
    b = Battery(seed=1)
    b.set_scenario("overcurrent")
    b.step(60.0, True, False, 0)                             # dis_on False
    assert b.current_a == 0.0


def test_leaving_a_forced_scenario_restores_the_normal_profile():
    b = Battery(seed=1, start_hour=2.0)                      # night: normal current is a small discharge
    b.set_scenario("charging")
    step(b)
    assert b.current_a > 5.0
    b.set_scenario("normal")
    assert b.force_c is None
    step(b)
    assert b.current_a < 1.0


def test_existing_scenarios_have_no_forced_current():
    for name in ("normal", "cell_drift", "overheat", "cloudy"):
        b = Battery(seed=1)
        b.set_scenario(name)
        assert b.force_c is None, name
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/firmware-rig && uv run pytest tests/test_battery_scenarios.py -q`
Expected: FAIL (`SCENARIOS` mismatch, `AttributeError: force_c`, unknown scenario `ValueError`).

- [ ] **Step 3: Implement in `services/firmware-rig/battery.py`**

3a. Replace the `SCENARIOS` line with:
```python
SCENARIOS = ("normal", "cell_drift", "overheat", "cloudy", "charging", "discharging", "low_soc",
             "overcurrent", "undervoltage", "instant_recovery")
```

3b. After the `CLOUD_FACTOR = 0.25 ...` constant add:
```python
CHARGING_C = 0.2                   # charging / discharging: the current is forced, as a C-rate (10 A at 50 Ah)
DISCHARGING_C = -0.2
LOW_SOC_RANGE = (0.10, 0.14)       # low_soc: where the cells start (true charge)
LOW_SOC_DRAIN_C = -0.01            # ...and a trickle load that keeps them there
OVERCURRENT_C = -1.2               # overcurrent: 1.2 C (60 A at 50 Ah); the chip trips discharge above 1 C
FORCED_C_RATE = {"charging": CHARGING_C, "discharging": DISCHARGING_C, "low_soc": LOW_SOC_DRAIN_C,
                 "overcurrent": OVERCURRENT_C, "instant_recovery": OVERCURRENT_C}
```

3c. In `Battery.__init__`, after `self.cloud = 1.0 ...` add:
```python
        self.force_c: float | None = None   # C-rate that replaces the solar/load profile (None = normal profile)
```

3d. In `wanted_current`, as the first statements after `c = self.nominal_ah ...` line (before `solar = ...`), add:
```python
        if self.force_c is not None:
            return self.force_c * c + self.rng.gauss(0.0, 0.004 * c)
```

3e. Replace `set_scenario` and `_apply_scenario` with:
```python
    def set_scenario(self, name: str) -> None:
        if name not in SCENARIOS:
            raise ValueError(f"unknown scenario {name!r}; choose from {SCENARIOS}")
        self.scenario, self.scenario_s = name, 0.0
        self.r = list(self.r_base)
        self.cloud = CLOUD_FACTOR if name == "cloudy" else 1.0
        self.force_c = FORCED_C_RATE.get(name)
        if name == "low_soc":
            self.soc = [self.rng.uniform(*LOW_SOC_RANGE) for _ in range(self.n)]

    def _apply_scenario(self, dt: float) -> None:
        """Run the handler `_scn_<name>` of the active scenario, if it has one (normal, charging, ... do not)."""
        self.scenario_s += dt
        handler = getattr(self, f"_scn_{self.scenario}", None)
        if handler is not None:
            handler(dt)
        if self.scenario != "overheat":
            self.ambient_c += (NORMAL_AMBIENT_C - self.ambient_c) * min(1.0, dt / 1800.0)

    def _scn_cell_drift(self, dt: float) -> None:
        hours = self.scenario_s / 3600.0
        k = DRIFT_CELL
        self.r[k] = self.r_base[k] + DRIFT_R_GROWTH_OHM_PER_H * hours
        self.soc[k] = max(-0.05, self.soc[k] - DRIFT_LEAK_A * dt / (self.cap[k] * 3600.0))

    def _scn_overheat(self, dt: float) -> None:
        rise = OVERHEAT_RATE_C_PER_MIN * self.scenario_s / 60.0
        self.ambient_c = min(OVERHEAT_MAX_AMBIENT_C, NORMAL_AMBIENT_C + rise)
```

- [ ] **Step 4: Run the new and the existing rig tests**

Run: `cd services/firmware-rig && uv run pytest tests -q`
Expected: all pass (integration tests skipped without `RIG_BINARY`).

- [ ] **Step 5: Commit**

```bash
git add services/firmware-rig/battery.py services/firmware-rig/tests/test_battery_scenarios.py
git commit -m "feat(rig): charging, discharging, low_soc and overcurrent scenarios" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `undervoltage` and `instant_recovery`

**Files:**
- Modify: `services/firmware-rig/battery.py`
- Modify: `services/firmware-rig/tests/test_battery_scenarios.py`

**Interfaces:**
- Consumes (Task 3): `Battery.force_c`, `FORCED_C_RATE["instant_recovery"]`, the `_scn_<name>` handler convention, `set_scenario`.
- Produces: constants `UV_LEAK_A`, `RECOVERY_FAULT_S`; handlers `_scn_undervoltage`, `_scn_instant_recovery`.

- [ ] **Step 1: Add the failing tests** (append to `services/firmware-rig/tests/test_battery_scenarios.py`)

```python
CUV = 1 << 2           # SAFETY_STATUS_A bit


def cell_voltages(b: Battery) -> list[float]:
    return [v for v in b.read_chip().cell_v if v > 0.5]


def test_undervoltage_collapses_one_cell_below_the_chip_limit():
    normal = Battery(seed=1)
    for _ in range(300):
        normal.step(60.0, True, True, 0)
    b = Battery(seed=1)
    b.set_scenario("undervoltage")
    for _ in range(300):
        b.step(60.0, True, True, 0)
        if min(cell_voltages(b)) < 2.5:
            break
    assert min(cell_voltages(b)) < 2.5
    assert min(cell_voltages(normal)) >= 2.5                 # a normal battery does not do that in 5 hours
    status_a, _b, _c = ChipProtections().update(b.read_chip())
    assert status_a & CUV


def test_undervoltage_works_on_a_small_pack():
    for cells in (2, 4):
        b = Battery(cells=cells, seed=1)
        b.set_scenario("undervoltage")
        for _ in range(300):
            b.step(60.0, True, True, 0)
        assert min(cell_voltages(b)) < 2.5, cells


def test_instant_recovery_raises_the_fault_then_returns_to_normal_by_itself():
    b = Battery(seed=1)
    b.set_scenario("instant_recovery")
    step(b)
    assert -b.current_a > b.nominal_ah                       # the surge
    protections = ChipProtections(capacity_ah=b.nominal_ah)
    assert protections.update(b.read_chip())[0] & OCD1
    step(b, 10)                                              # 11 minutes in total: past the 600 s surge
    assert b.scenario == "normal" and b.force_c is None
    step(b)
    assert -b.current_a < b.nominal_ah
    assert not protections.update(b.read_chip())[0] & OCD1   # the latch released
```
- [ ] **Step 2: Run to verify they fail**

Run: `cd services/firmware-rig && uv run pytest tests/test_battery_scenarios.py -q`
Expected: the three new tests FAIL (the scenarios currently behave like `normal`).

- [ ] **Step 3: Implement in `services/firmware-rig/battery.py`**

Add constants after `FORCED_C_RATE`:
```python
UV_LEAK_A = 30.0                   # undervoltage: one cell leaks this much until it collapses
RECOVERY_FAULT_S = 600.0           # instant_recovery: simulated seconds of surge (about 10 s real at SPEED=60)
```
Add handlers after `_scn_overheat`:
```python
    def _scn_undervoltage(self, dt: float) -> None:
        k = min(DRIFT_CELL, self.n - 1)
        self.soc[k] = max(-0.05, self.soc[k] - UV_LEAK_A * dt / (self.cap[k] * 3600.0))

    def _scn_instant_recovery(self, dt: float) -> None:
        if self.scenario_s >= RECOVERY_FAULT_S:
            self.set_scenario("normal")
```

- [ ] **Step 4: Run all rig tests**

Run: `cd services/firmware-rig && uv run pytest tests -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add services/firmware-rig/battery.py services/firmware-rig/tests/test_battery_scenarios.py
git commit -m "feat(rig): undervoltage and instant_recovery scenarios" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Keep the pure-Python simulator in sync and cover the API

**Files:**
- Modify: `services/bms-simulator/battery.py` (replace with the rig's copy)
- Modify: `services/bms-simulator/tests/test_battery.py` (the `SCENARIOS` assertion at line ~88)
- Create: `services/bms-simulator/tests/test_battery_scenarios.py` (physical checks only: the simulator has no `protections.py`)
- Modify: `services/firmware-rig/tests/test_api.py` (new names listed in the 400)

**Interfaces:**
- Consumes (Tasks 3-4): the final `services/firmware-rig/battery.py`.
- Produces: identical `battery.py` in both services.

- [ ] **Step 1: Write the failing tests**

In `services/bms-simulator/tests/test_battery.py` change the assertion in `test_scenarios_are_known_and_unknown_ones_rejected` to:
```python
    assert SCENARIOS == ("normal", "cell_drift", "overheat", "cloudy", "charging", "discharging", "low_soc",
                         "overcurrent", "undervoltage", "instant_recovery")
```
Create `services/bms-simulator/tests/test_battery_scenarios.py`:
```python
"""The new scenarios on the simulator's battery (same physics as the firmware rig's; see the rig's tests)."""
from battery import Battery


def lowest_cell(b: Battery) -> float:
    return min(v for v in b.read_chip().cell_v if v > 0.5)


def test_forced_current_scenarios():
    for name, sign in (("charging", 1), ("discharging", -1)):
        b = Battery(seed=1, start_hour=2.0)
        b.set_scenario(name)
        b.step(60.0, True, True, 0)
        assert sign * b.current_a > 5.0, name


def test_overcurrent_exceeds_one_c():
    b = Battery(seed=1)
    b.set_scenario("overcurrent")
    b.step(60.0, True, True, 0)
    assert -b.current_a > b.nominal_ah


def test_low_soc_starts_nearly_empty():
    b = Battery(seed=1, start_hour=12.0)
    b.set_scenario("low_soc")
    for _ in range(60):
        b.step(60.0, True, True, 0)
    assert max(b.soc) < 0.15


def test_undervoltage_collapses_a_cell_even_on_a_small_pack():
    b = Battery(cells=2, seed=1)
    b.set_scenario("undervoltage")
    for _ in range(300):
        b.step(60.0, True, True, 0)
    assert lowest_cell(b) < 2.5


def test_instant_recovery_returns_to_normal_by_itself():
    b = Battery(seed=1)
    b.set_scenario("instant_recovery")
    for _ in range(11):
        b.step(60.0, True, True, 0)
    assert b.scenario == "normal" and b.force_c is None
```
In `services/firmware-rig/tests/test_api.py`, at the end of `test_scenario_can_be_switched_and_unknown_ones_are_refused` add:
```python
    assert c.post("/scenario/undervoltage").json() == {"scenario": "undervoltage"}
    assert "instant_recovery" in refused.json()["detail"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/bms-simulator && uv run pytest tests -q`
Expected: FAIL (old `battery.py`).

- [ ] **Step 3: Sync `battery.py`**

```bash
cp services/firmware-rig/battery.py services/bms-simulator/battery.py
diff services/firmware-rig/battery.py services/bms-simulator/battery.py && echo identical
```
Expected: `identical`.

- [ ] **Step 4: Run both test suites**

```bash
cd services/bms-simulator && uv run pytest tests -q
cd ../firmware-rig && uv run pytest tests -q
```
Expected: all pass. `test_sim.py::test_every_snapshot_matches_the_schema_in_every_scenario` now also runs every new scenario through the schema check; if a scenario produces an out-of-range value (for example SoC below 0 in the snapshot), fix the value in `Simulator`/mapping rather than weakening the schema, and report it.

- [ ] **Step 5: Commit**

```bash
git add services/bms-simulator services/firmware-rig/tests/test_api.py
git commit -m "feat(sim): sync new scenarios into the pure-Python simulator" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Docs and a check against the real firmware

**Files:**
- Modify: `README.md` (lines 48 and 70)
- Modify: `.env.example` (the `SCENARIO` comment)
- Modify: `services/firmware-rig/readme.md` (add a scenario table after the "For a fault demo, speed the battery up" paragraph)
- Modify: `TODO.txt` (remove done items 1 and 4)

- [ ] **Step 1: Edit the docs**

- `README.md` line 48: replace `scenarios \`normal\`, \`cell_drift\`, \`overheat\`, \`cloudy\`` with `scenarios \`normal\`, \`charging\`, \`discharging\`, \`low_soc\`, \`cell_drift\`, \`overheat\`, \`cloudy\`, \`overcurrent\`, \`undervoltage\`, \`instant_recovery\``.
- `README.md` line 70: replace `Scenarios: \`normal\`, \`cell_drift\`, \`overheat\`, \`cloudy\`.` with the same list.
- `.env.example`: replace `# normal | cell_drift | overheat | cloudy   (can also be switched live: POST /scenario/<name>)` with `# normal | charging | discharging | low_soc | cell_drift | overheat | cloudy | overcurrent | undervoltage | instant_recovery   (can also be switched live: POST /scenario/<name>)`.
- `services/firmware-rig/readme.md`: after the "For a fault demo..." paragraph insert the six-row table from the "Part B" intro of this plan under a `### Scenarios` heading, plus the sentence: "`normal`, `cell_drift`, `overheat` and `cloudy` are described in the model comments in `battery.py`. `overcurrent` and `instant_recovery` open the discharge FET once the firmware trips, so the current drops to zero afterwards, as on a real pack."
- `TODO.txt`: delete the lines for items 1 and 4 (leave 2 and 3).

- [ ] **Step 2: Verify with the real firmware (manual; needs Docker, Linux/amd64 build)**

```bash
cp .env.example .env
docker compose up --build -d
for s in overcurrent undervoltage instant_recovery; do
  curl -s -X POST http://localhost:8010/scenario/$s; echo
  sleep 90
  docker compose logs --since 100s bms-bridge | grep -i -E "event|fault" | tail -5
  curl -s -X POST http://localhost:8010/scenario/normal; echo
  sleep 60
done
```
Expected, per scenario (at the default `SPEED=60`): `overcurrent` raises a discharge over-current fault event; `undervoltage` raises a cell under-voltage fault event; `instant_recovery` raises a fault event and a cleared event without switching back by hand. Record the exact event names you see in the firmware-rig readme table's "Expected visible effect" column, replacing any guess. If a scenario raises nothing, report which one and the bridge log; do not change protection limits to force it.

- [ ] **Step 3: Commit**

```bash
git add README.md .env.example services/firmware-rig/readme.md TODO.txt
git commit -m "docs: document the new scenarios and tick off TODO items 1 and 4" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review

- **Spec coverage:** Item 4 (reproduce, `MAIL_MAILER=log` via the override file, registration works): Tasks 1-2. Item 1 (all eight scenarios: `charging`, `discharging`, `low_soc`, `overcurrent`, `undervoltage`, `instant_recovery` new; `cell_drift`, `cloudy` already exist; per-scenario tests asserting the chip fault; handler table replacing the `if` chain; simulator names mirrored; docs updated): Tasks 3-6. The spec's "firmware raises the matching fault" is covered at chip level in unit tests (Tasks 3-4) and at firmware level manually in Task 6, because the real firmware needs a Linux build.
- **Placeholders:** none; the one deliberate open item is the exact firmware event names, which Task 6 Step 2 records from observation.
- **Types:** `force_c`, `FORCED_C_RATE`, `_scn_<name>`, `UV_LEAK_A`, `RECOVERY_FAULT_S`, `disable_mail`, `MAIL_OVERRIDE` are named identically wherever used.
- **Known caveat:** `instant_recovery`'s 600 simulated seconds is about 10 real seconds at `SPEED=60`, so a relay publishing every 5 s sees the fault; at `SPEED=1` it lasts 10 minutes.
