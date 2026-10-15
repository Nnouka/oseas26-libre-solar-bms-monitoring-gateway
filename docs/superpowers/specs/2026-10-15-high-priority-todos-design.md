# High-priority TODOs: design

Date: 2026-10-15. Source: `TODO.txt` (High Priority, items 1-4).

## Intent

Make the demo stack easier to run and richer to show: more battery scenarios, MPM that starts from the root compose without SMTP problems, and a Battery Health page that lets an operator review the full history of summaries and events.

Success: `docker compose` brings up the whole stack including MPM with the plugin; every listed scenario can be triggered and produces the expected firmware fault; the operator can browse any battery's summaries and events by time range.

## Order and sizing

4 (S) -> 1 (M) -> 2 (L) -> 3 (L). Item 4 unblocks 2; item 1 feeds the demo for 3. Each item gets its own implementation plan.

## Item 4: Local MPM without SMTP

- MPM sends mail with `Mail::to()->queue()` (`App\Helpers\MailHelper`), configured by `MAIL_*` in its backend env. MPM's compose reads an optional `.env.override.micropowermanager-backend`.
- Set `MAIL_MAILER=log` through that override file, written by the setup script (item 2's build, or `apply.py` until then).
- First reproduce the company-registration failure to confirm SMTP is the cause. If it is not, fix the real cause instead.
- Done when: registering a company in a fresh MPM succeeds with no mail server; mails appear in the Laravel log.

## Item 1: More scenarios

Add `charging`, `discharging`, `low_soc`, `cell_drift` (exists), `cloudy` (exists), `overcurrent`, `undervoltage`, `instant_recovery` to `services/firmware-rig/battery.py`.

- Replace the `if` chain in `_apply_scenario` with a per-scenario table of small functions.
- `charging` / `discharging`: force the sign of the wanted current. `low_soc`: start cells near 10 %. `undervoltage`: drain one cell below the chip UV threshold. `overcurrent`: a load surge above the chip limit. `instant_recovery`: run a fault, then return to `normal` on its own.
- `SCENARIOS` and `/scenario/{name}` validation stay the single source of truth. Mirror the names in `services/bms-simulator`.
- Each scenario gets a test asserting the expected firmware fault or state. Update the README scenario lists and `.env.example`.

## Item 2: One-build MPM image

Chosen approach (A): an `mpm` profile in the root `docker-compose.yml`. A Dockerfile clones upstream MPM at a pinned commit (sparse checkout), runs `apply.py` and `fix_composer_dockerfiles.py`, forces LF line endings, and applies the item 4 mail setting. `docker compose --profile mpm up --build` starts the full stack.

- Open points for the plan: how MPM's own services (mysql, redis, queue worker, scheduler, frontend) are expressed in our compose; how the API key is created without a manual step; how `MPM_BASE_URL` changes from `host.docker.internal` to the service name.
- Riskiest item; the plan starts with a spike to confirm MPM builds from a Dockerfile clone.

## Item 3: Richer Battery Health page (plugin only)

No bridge or ingestor changes, so only fields already in a summary are available (state, SoC, SoH, cycles, pack voltage, imbalance, max temp, health, score, reasons, active faults). Events are already stored one row each, idempotently. The gap is that summaries overwrite one row per device.

### Backend

- New tenant migration (the existing one is not edited): `libre_solar_bms_summaries`, one row per summary with the payload fields, `libre_solar_bms_device_id`, `reported_at`, and a unique `summary_key` = sha1(device_id|ts). Index on `(device_id, reported_at)`.
- `LibreSolarBmsService::recordSummary` writes the history row before the stale check. A late replayed summary is kept in history but does not overwrite the current device row.
- `GET /batteries/{id}/summaries`: `from`, `to`, `health`, `page`, `per_page`; newest first.
- `GET /batteries/{id}/events`: add `from`, `to`, `severity`, `type`, paging. `limit` keeps working.
- Make sure `apply.py` copies the new migration and files.

### Frontend (battery detail page, tabs)

- Overview: current banner and tiles, active-fault chips, time since last report, trend charts (SoC, pack voltage, max temp, imbalance) over 24h / 7d / custom. Inline SVG, no new dependency.
- History: paginated table of all summaries with time-range and health filters; row click shows reasons and faults.
- Events: fault log with time-range, severity and type filters, paging, recommended action.
- The list page is unchanged.

### Testing

Backend feature tests: idempotent ingest, out-of-order handling, filters, paging. Frontend: manual check against the running rig.

### Out of scope

Per-cell voltages (needs a bridge change), retention pruning (about 35k rows per device per year at 15-minute summaries), CSV export. Each is easy to add later.

## Risks

- Item 2 depends on upstream MPM structure; pin the commit and re-check on bumps.
- Item 4's root cause is assumed, not yet reproduced.
- Item 1 scenario thresholds depend on the firmware's chip limits in `rig.conf`; tests must confirm each fault actually triggers.
