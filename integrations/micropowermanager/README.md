# MicroPowerManager (MPM) integration

This folder adds a **Battery Health** page to MicroPowerManager. It is an overlay: we keep our plugin code here and a script copies it into a normal MPM checkout.

```
BMS simulator -> Mosquitto -> bridge -> mpm-ingestor -> MPM (Battery Health page)
                                    \-> Home Assistant (full data stream)
```

The `mpm-ingestor` service (in `services/mpm-ingestor/`) reads the low-frequency `bms/+/summary` and `bms/+/event` topics and posts them to the MPM plugin over HTTP.

## What is in this folder

| Path | Purpose |
|------|---------|
| `backend/` | Laravel plugin `LibreSolarBms`: migrations, ingest and read API, models, service |
| `frontend/` | Vue pages: battery list and battery detail (health banner, metrics, fault log) |
| `apply.py` | Copies the plugin into an MPM checkout and patches the few MPM files that must know about it (safe to run again) |
| `fix_composer_dockerfiles.py` | Works around networks that block `composer.github.io` during the MPM Docker build |

## Ports

| Service | Host port |
|---------|-----------|
| MPM backend | 8000 |
| MPM frontend | 8001 |
| MPM operator dashboard | 8002 |
| Simulator | 8010 |
| Bridge | 8011 |
| MPM ingestor | 8012 |
| Home Assistant | 8123 |

## Quick start (one command)

From the root of this repo:

```bash
docker compose --profile mpm up --build -d
```

This builds MPM with the Battery Health plugin from a pinned upstream commit (`MPM_REF` in `.env`), starts it next to the gateway, creates a company and an owner, and gives `mpm-ingestor` an API key automatically. The first build takes about 10-20 minutes (PHP, Composer, the Vue build); later starts are quick.

- MPM: http://localhost:8001 (backend API on 8000). Sign in with `MPM_OWNER_EMAIL` / `MPM_OWNER_PASSWORD` from `.env` (demo defaults: `owner@example.com` / `demo-password-1`; change them in `.env` before first start if others can reach the machine).
- Battery Health, Batteries: `haske-bms-001` appears within a minute.
- Data lives in the `mpm-*` Docker volumes. To reset only MPM: `docker compose --profile mpm rm -sf mpm-backend mpm-queue-worker mpm-scheduler mpm-frontend mpm-mysql mpm-redis mpm-bootstrap`, then `docker volume rm <project>_mpm-mysql-data <project>_mpm-storage <project>_mpm-shared`. (`docker compose down -v` would also delete the Home Assistant and Mosquitto volumes.)
- Stop any separately running MPM first: both use ports 8000 and 8001.
- The key file written by `mpm-bootstrap` wins over any `MPM_API_KEY` in `.env`, so an old key there does not matter with this profile.

## Manual setup (to develop the plugin with hot reload; about 30 minutes the first time)

The quick start above builds MPM from a pinned upstream commit. Use this route when you want to edit the plugin and see changes live.

Run MPM in a **separate folder** next to this repo, not inside it.

### 1. Get MPM

```powershell
git clone --filter=blob:none --no-checkout https://github.com/EnAccess/micropowermanager.git
cd micropowermanager
git sparse-checkout set --cone src dev docker
git checkout
```

The sparse checkout is needed on Windows: MPM contains a folder whose name ends with a space, which makes a normal clone fail. Do not run `git restore` afterwards.

### 2. Apply our plugin

From the root of **this** repo:

```powershell
python integrations/micropowermanager/apply.py "<path to micropowermanager>"
python integrations/micropowermanager/fix_composer_dockerfiles.py "<path to micropowermanager>"
```

### 3. Windows fix: line endings

Inside the MPM folder, convert shell scripts and env files to LF, or containers exit with "no such file or directory":

```powershell
python -c "import pathlib;[p.write_bytes(p.read_bytes().replace(b'\r\n',b'\n')) for g in ('docker/*.sh','dev/.env*') for p in pathlib.Path('.').glob(g)]"
```

### 4. Start MPM

Follow MPM's own dev instructions (`docker compose` in the MPM `dev` folder). Wait until all containers are healthy. If the frontend was already running when you applied the plugin, restart `frontend-dev` and give it a few minutes to recompile.

Open http://localhost:8001, register a company, and tick **LibreSolarBms** in the plugin step. An owner account is created there.

### 5. Create an API key

In MPM go to Settings, then API keys, create a key, and copy it.

### 6. Start our stack

In this repo, copy `.env.example` to `.env` (the real file is git-ignored) and set:

```
MPM_BASE_URL=http://host.docker.internal:8000
MPM_API_KEY=<the key from step 5>
```

Never put the real key in `.env.example` or in screenshots.

```powershell
docker compose up --build -d
```

### 7. Check it

Open http://localhost:8001 and click **Battery Health, Batteries**. A battery named `haske-bms-001` should appear within a minute. Click its row for the detail page.

Try a fault:

```powershell
curl -X POST http://localhost:8010/scenario/overheat
curl -X POST http://localhost:8010/scenario/normal
```

The list should go Needs attention, then Critical, and the detail page shows the fault log with recommended actions. The page refreshes every 30 seconds.

## How it works

- Ingest endpoints: `POST /api/libre-solar-bms/ingest/summary` and `/events`, protected by an MPM API key (`Authorization: Bearer ...`).
- Read endpoints for the UI: `/api/libre-solar-bms/batteries`, `/batteries/{id}`, `/batteries/{id}/events`.
- Old summaries are ignored, and events are stored once (idempotent), so re-delivery is safe.
- The ingestor uses a persistent MQTT session and an on-disk outbox, so data is not lost if MPM is down for a while.

## Troubleshooting

- **Sidebar shows `menu.Battery Health`**: re-run `apply.py` (it adds the translations), then restart `frontend-dev`.
- **Page is blank right after a restart**: the frontend is still compiling. Wait.
- **"No batteries yet"**: check that `mpm-ingestor` is running (`docker compose logs mpm-ingestor`) and that `MPM_API_KEY` is correct.
- **Docker build fails with "Invalid installer checksum"**: run `fix_composer_dockerfiles.py` (step 2).
- **`ResizeObserver loop` overlay in the browser**: harmless, dismiss it.

## Known limits

- Health and state-of-health are estimates from a simple cycle model.
- The plugin id (33) could clash with a future upstream plugin.
- The MQTT broker is anonymous, which is fine for the demo only.
