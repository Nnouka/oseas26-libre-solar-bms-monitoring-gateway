# One-Build MPM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docker compose --profile mpm up --build -d` from the repo root starts MicroPowerManager (MPM) with the Libre Solar BMS plugin installed, a company and owner created, and an API key handed to `mpm-ingestor` automatically. No sibling clone, no `apply.py` run, no manual key.

**Architecture:** One multi-stage Dockerfile (`integrations/micropowermanager/docker/Dockerfile`). Stage `mpm-source` clones upstream MPM at a pinned commit (sparse) and runs our existing `apply.py` on it. Stage `backend` is one PHP image reused by three compose services (web, queue worker, scheduler) by overriding the entrypoint. Stage `frontend` builds the Vue app into nginx. A small stdlib-only `bootstrap.py` registers the company, signs in, creates an API key and writes it to a shared volume; `mpm-ingestor` reads the key from that file when `MPM_API_KEY` is empty.

**Tech Stack:** Docker Compose (profiles), multi-stage Docker builds, PHP 8.5/Apache (MPM), Node 20 + nginx (MPM frontend), MySQL 8.4, Redis 7, Python 3.12 stdlib (bootstrap), pytest/uv.

**Spec:** `docs/superpowers/specs/2026-10-15-high-priority-todos-design.md` (section "Item 2"). Item 4 was dropped (MPM registration was verified working without changes), so the spec's dependency of item 2 on item 4 no longer applies.

## Global Constraints

- Single command from the repo root: `docker compose --profile mpm up --build -d`. Without `--profile mpm` the stack behaves exactly as today (no new default services, no new required env vars).
- MPM source is pinned: `MPM_REF=d6632495586a9dc5c4f71decdec33f5f960701b8` (upstream `origin/main` at the time of writing; overridable with the `MPM_REF` build arg).
- No change to MPM source other than what `integrations/micropowermanager/apply.py` already does. Do not run or keep `fix_composer_dockerfiles.py` in this path (our Dockerfile takes Composer from the `composer:2.8.8` image instead of downloading its installer).
- Host ports: MPM backend `8000`, MPM frontend `8001` (same as the manual setup; `mpm-ingestor` already defaults to `MPM_BASE_URL=http://host.docker.internal:8000`, which keeps working).
- MySQL and Redis are not published to the host.
- Demo credentials only: the owner login comes from `.env` (`MPM_OWNER_EMAIL`, `MPM_OWNER_PASSWORD`) with the demo defaults below; the database password from `MPM_DB_PASSWORD`. Never commit a real secret. The upstream dev `APP_KEY`/`JWT_SECRET` values are public dev defaults and are marked demo-only.
- Commit messages end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Service tests: `cd services/mpm-ingestor && uv run pytest tests -q`; bootstrap tests: `uv run --no-project --with pytest python -m pytest integrations/micropowermanager/tests -q` from the repo root.

## Review Focus

1. Re-running `up` (containers recreated, volumes kept) must not create a second company or key: bootstrap sees the company exists, sees the stored key still works, and exits 0. (Task 4)
2. MPM database wiped but the shared key volume kept (or the reverse): a stale key (HTTP 401) is replaced; a missing key file is created. (Task 4)
3. Backend never becomes ready: bootstrap must fail with a clear message and non-zero exit, not hang forever. (Task 4)
4. `mpm-ingestor` started before the key exists: it must queue (as today) and start delivering once the file appears, without a restart. (Task 5)
5. Stack started WITHOUT the profile and with `MPM_API_KEY` set in `.env`: unchanged behaviour (the env key wins over the file). (Task 5)
6. First boot is slow (central and per-company migrations take minutes): healthchecks must have a long enough `start_period`, and bootstrap's registration request a long enough timeout (observed: about 2 minutes). (Tasks 3, 4)

---

### Task 1: Dockerfile stages `mpm-source` and `backend`

**Files:**
- Create: `integrations/micropowermanager/docker/Dockerfile`

**Interfaces:**
- Produces: build arg `MPM_REF`; stage `mpm-source` (patched MPM tree at `/mpm`); stage `backend` (PHP image with MPM at `/var/www/html`, entrypoints `/usr/local/bin/entrypoint-backend.sh`, `/usr/local/bin/entrypoint-queue-worker.sh`, `/usr/local/bin/entrypoint-scheduler.sh`). Build context is the **repo root**.

- [ ] **Step 1: Write the Dockerfile (first two stages)**

```dockerfile
# syntax=docker/dockerfile:1
# Builds MicroPowerManager with the Libre Solar BMS plugin from upstream, in one build.
# Build context: the repo root.   docker build -f integrations/micropowermanager/docker/Dockerfile --target backend .

ARG MPM_REF=d6632495586a9dc5c4f71decdec33f5f960701b8

# ---- upstream MPM + our plugin overlay -------------------------------------------------------
FROM python:3.12-slim AS mpm-source
ARG MPM_REF
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /mpm
RUN git init -q . \
    && git remote add origin https://github.com/EnAccess/micropowermanager.git \
    && git sparse-checkout set --cone src docker \
    && git fetch -q --depth 1 --filter=blob:none origin "${MPM_REF}" \
    && git checkout -q FETCH_HEAD
COPY integrations/micropowermanager/apply.py /overlay/apply.py
COPY integrations/micropowermanager/backend /overlay/backend
COPY integrations/micropowermanager/frontend /overlay/frontend
RUN python /overlay/apply.py /mpm

# ---- backend image: web, queue worker and scheduler all run from this one image ------------------
FROM php:8.5-apache AS backend
RUN apt-get update && apt-get install -y --no-install-recommends \
        git gosu libfreetype6-dev libjpeg-dev libssl-dev libxml2-dev libzip-dev unzip zlib1g-dev \
        supervisor cron \
    && rm -rf /var/lib/apt/lists/*
RUN docker-php-ext-install bcmath exif gd pcntl pdo pdo_mysql zip
RUN docker-php-ext-configure gd --with-freetype --with-jpeg
RUN docker-php-ext-configure pcntl --enable-pcntl
COPY --from=ghcr.io/php/pie:bin /pie /usr/bin/pie
RUN pie install phpredis/phpredis:6.3.0 && docker-php-ext-enable redis
COPY --from=composer:2.8.8 /usr/bin/composer /usr/local/bin/composer

COPY --from=mpm-source /mpm/docker/config/php/php.ini /usr/local/etc/php/php.ini
COPY --from=mpm-source /mpm/docker/config/apache2/sites-enabled/000-default.conf /etc/apache2/sites-enabled/
RUN a2enmod rewrite
COPY --from=mpm-source /mpm/docker/config/supervisor/ /etc/supervisor/conf.d/
COPY --from=mpm-source /mpm/docker/config/cron/crontab /etc/cron.d/cron
RUN chmod 0644 /etc/cron.d/cron /etc/supervisor \
    && mkdir -p /etc/supervisor/logs/horizon \
    && touch /etc/supervisor/logs/supervisord.log /etc/supervisor/logs/horizon/out.log \
             /etc/supervisor/logs/horizon/err.log /var/log/cron.log

COPY --from=mpm-source /mpm/src/backend /var/www/html
WORKDIR /var/www/html
RUN composer install --no-dev --optimize-autoloader

COPY --from=mpm-source /mpm/docker/entrypoint-backend.sh /usr/local/bin/entrypoint-backend.sh
COPY --from=mpm-source /mpm/docker/entrypoint-queue-worker.sh /usr/local/bin/entrypoint-queue-worker.sh
COPY --from=mpm-source /mpm/docker/entrypoint-scheduler.sh /usr/local/bin/entrypoint-scheduler.sh
RUN chmod +x /usr/local/bin/entrypoint-*.sh
VOLUME ["/var/www/html/storage"]
ENTRYPOINT ["/usr/local/bin/entrypoint-backend.sh"]
EXPOSE 80
```

- [ ] **Step 2: Build the `mpm-source` stage and inspect it**

Run (repo root): `docker build -f integrations/micropowermanager/docker/Dockerfile --target mpm-source -t mpm-source-probe .`
Expected: success; last lines show `apply.py` output including `copied plugin files` and `patched: MpmPlugin.php` (or `already patched`).
Then: `docker run --rm mpm-source-probe sh -c "ls src/backend/app/Plugins/LibreSolarBms && grep -c LIBRE_SOLAR_BMS src/backend/app/Models/MpmPlugin.php"`
Expected: the plugin folders listed and a count of at least `1`.

- [ ] **Step 3: Build the `backend` stage**

Run: `docker build -f integrations/micropowermanager/docker/Dockerfile --target backend -t mpm-backend-probe .`
Expected: success (several minutes: PHP extensions plus `composer install`). If `composer install` fails on a platform requirement, record the exact error and report; do not edit upstream code. If the phpredis or `pie` step fails, compare with `docker/DockerfileBackendProd` in the pinned MPM tree and mirror its exact lines.

- [ ] **Step 4: Check the image contains what the services will need**

```bash
docker run --rm --entrypoint sh mpm-backend-probe -c "php -v | head -1; ls /usr/local/bin/entrypoint-*.sh; which supervisord cron gosu; ls /var/www/html/app/Plugins | grep -c LibreSolarBms; php artisan --version"
```
Expected: PHP 8.5.x, the three entrypoints, the three binaries, `1`, and a Laravel version line. (`artisan --version` may complain about the database; the version line is what matters.)

- [ ] **Step 5: Commit**

```bash
git add integrations/micropowermanager/docker/Dockerfile
git commit -m "feat(mpm): Dockerfile that builds MPM with the plugin from a pinned upstream commit" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Dockerfile stage `frontend`

**Files:**
- Modify: `integrations/micropowermanager/docker/Dockerfile` (append a stage)

**Interfaces:**
- Consumes (Task 1): stage `mpm-source`.
- Produces: stage `frontend` (nginx serving the built Vue app; entrypoint injects `MPM_BACKEND_URL` and `MPM_ENV` at start from container env, as upstream does).

- [ ] **Step 1: Append the stage**

```dockerfile

# ---- frontend: Vue app built into nginx --------------------------------------------------------------
FROM node:20.18-alpine3.20 AS frontend-build
WORKDIR /app
COPY --from=mpm-source /mpm/src/frontend/package*.json ./
RUN npm install
COPY --from=mpm-source /mpm/src/frontend .
RUN npm run build
# single executable application for runtime env injection (same as upstream DockerfileFrontendProd)
RUN echo '{ "main": "node_modules/@import-meta-env/cli/bin/import-meta-env.js", "output": "sea-prep.blob" }' > sea-config.json \
    && node --experimental-sea-config sea-config.json \
    && cp "$(command -v node)" import-meta-env-alpine \
    && npx postject import-meta-env-alpine NODE_SEA_BLOB sea-prep.blob --sentinel-fuse NODE_SEA_FUSE_fce680ab2cc467b6e072b8b5df1996b2

FROM nginx:1.27-alpine3.20 AS frontend
RUN apk add libstdc++
COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY --from=frontend-build /app/import-meta-env-alpine /usr/local/bin/
COPY --from=frontend-build /app/.env.example /usr/share/nginx/html/
COPY --from=mpm-source /mpm/docker/entrypoint-frontend-prod.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint-frontend-prod.sh
ENTRYPOINT ["/usr/local/bin/entrypoint-frontend-prod.sh"]
EXPOSE 80
```
Note: the final stage order matters for `docker build` without `--target`; compose will always name a target.

- [ ] **Step 2: Build it**

Run: `docker build -f integrations/micropowermanager/docker/Dockerfile --target frontend -t mpm-frontend-probe .`
Expected: success (a few minutes; the Vue build must include our plugin pages, so a failure here means the overlay's Vue files have an error: report it).

- [ ] **Step 3: Check the plugin pages are in the bundle**

```bash
docker run --rm --entrypoint sh mpm-frontend-probe -c "grep -l -r 'Battery Health' /usr/share/nginx/html | head -3"
```
Expected: at least one built JS or HTML file is listed.

- [ ] **Step 4: Commit**

```bash
git add integrations/micropowermanager/docker/Dockerfile
git commit -m "feat(mpm): frontend build stage" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Compose services (profile `mpm`) and environment

**Files:**
- Modify: `docker-compose.yml` (new services and volumes)
- Create: `integrations/micropowermanager/docker/backend.env`
- Modify: `.env.example`

**Interfaces:**
- Consumes (Tasks 1-2): Dockerfile targets `backend`, `frontend`.
- Produces: services `mpm-mysql`, `mpm-redis`, `mpm-backend` (8000), `mpm-queue-worker`, `mpm-scheduler`, `mpm-frontend` (8001); volumes `mpm-mysql-data`, `mpm-storage`, `mpm-shared`; the image tag `libre-solar-mpm-backend`. `mpm-bootstrap` is added in Task 6.

- [ ] **Step 1: Create `integrations/micropowermanager/docker/backend.env`**

Start from the pinned upstream file `dev/.env.micropowermanager-backend` (read it from the clone at `C:\hackathons\en-access\micropowermanager\dev\.env.micropowermanager-backend`, or from `https://github.com/EnAccess/micropowermanager/blob/d6632495586a9dc5c4f71decdec33f5f960701b8/dev/.env.micropowermanager-backend`) and keep its `APP_KEY`, `JWT_SECRET`, `LOG_*`, `PUSHER_*`, `BROADCAST_DRIVER`, `CACHE_DRIVER` lines verbatim. Set these values (override or add):

```
# Demo-only values for the docker compose `mpm` profile. Not for production.
APP_ENV=development
APP_DEBUG=false
MPM_LOAD_DEMO_DATA=false
MPM_FORCE_OPTIMIZE=false
TELESCOPE_ENABLED=false
MPM_FRONTEND_URL=http://localhost:8001
DB_CONNECTION=micro_power_manager
DB_HOST=mpm-mysql
DB_PORT=3306
DB_DATABASE=micro_power_manager
DB_USERNAME=root
REDIS_HOST=mpm-redis
```
Do NOT put `DB_PASSWORD` or `REDIS_PASSWORD` in the file; compose supplies them (Step 2). Put a first-line comment saying the keys are upstream's public dev defaults.

- [ ] **Step 2: Add the services to `docker-compose.yml`**

Add under `services:` (keep existing services untouched):
```yaml
  # ---- MicroPowerManager with the Libre Solar BMS plugin: docker compose --profile mpm up --build -d ----
  mpm-mysql:
    profiles: ["mpm"]
    image: mysql:8.4
    environment:
      MYSQL_DATABASE: micro_power_manager
      MYSQL_ROOT_PASSWORD: ${MPM_DB_PASSWORD:-mpm-demo-db-password}
    volumes:
      - mpm-mysql-data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost"]
      start_period: 20s
      interval: 10s
      timeout: 5s
      retries: 6
    restart: unless-stopped

  mpm-redis:
    profiles: ["mpm"]
    image: redis:7
    restart: unless-stopped

  mpm-backend:
    profiles: ["mpm"]
    image: libre-solar-mpm-backend
    build:
      context: .
      dockerfile: integrations/micropowermanager/docker/Dockerfile
      target: backend
      args:
        MPM_REF: ${MPM_REF:-d6632495586a9dc5c4f71decdec33f5f960701b8}
    env_file: ./integrations/micropowermanager/docker/backend.env
    environment:
      DB_PASSWORD: ${MPM_DB_PASSWORD:-mpm-demo-db-password}
    volumes:
      - mpm-storage:/var/www/html/storage
    ports:
      - "8000:80"
    depends_on:
      mpm-mysql: {condition: service_healthy}
      mpm-redis: {condition: service_started}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost/up"]
      start_period: 300s          # first boot runs central and tenant migrations
      interval: 15s
      timeout: 10s
      retries: 40
    restart: unless-stopped

  mpm-queue-worker:
    profiles: ["mpm"]
    image: libre-solar-mpm-backend
    entrypoint: ["/usr/local/bin/entrypoint-queue-worker.sh"]
    env_file: ./integrations/micropowermanager/docker/backend.env
    environment:
      DB_PASSWORD: ${MPM_DB_PASSWORD:-mpm-demo-db-password}
    volumes:
      - mpm-storage:/var/www/html/storage
    depends_on:
      mpm-backend: {condition: service_healthy}
    restart: unless-stopped

  mpm-scheduler:
    profiles: ["mpm"]
    image: libre-solar-mpm-backend
    entrypoint: ["/usr/local/bin/entrypoint-scheduler.sh"]
    env_file: ./integrations/micropowermanager/docker/backend.env
    environment:
      DB_PASSWORD: ${MPM_DB_PASSWORD:-mpm-demo-db-password}
    volumes:
      - mpm-storage:/var/www/html/storage
    depends_on:
      mpm-backend: {condition: service_healthy}
    restart: unless-stopped

  mpm-frontend:
    profiles: ["mpm"]
    build:
      context: .
      dockerfile: integrations/micropowermanager/docker/Dockerfile
      target: frontend
      args:
        MPM_REF: ${MPM_REF:-d6632495586a9dc5c4f71decdec33f5f960701b8}
    environment:
      MPM_ENV: development
      MPM_BACKEND_URL: http://localhost:8000       # the browser calls the backend on the host port
    ports:
      - "8001:80"
    restart: unless-stopped
```
and under `volumes:` add `mpm-mysql-data:`, `mpm-storage:` and `mpm-shared:`.
`mpm-backend` runs migrations in its entrypoint; the worker and scheduler wait for it to be healthy so they never race the migrations. (Redis: MPM's dev env sets `REDIS_PASSWORD`; the stock redis image has none, which Laravel accepts when `REDIS_PASSWORD` is unset; the file in Step 1 must therefore not set it.)

- [ ] **Step 3: Document the variables in `.env.example`**

Append:
```
# --- MicroPowerManager (only with: docker compose --profile mpm up --build -d) ---
# Demo defaults; change them if the stack is reachable by anyone else.
# Owner login created in MPM on first start (http://localhost:8001)
MPM_OWNER_EMAIL=owner@example.com
MPM_OWNER_PASSWORD=demo-password-1
# MySQL root password used by the MPM containers
MPM_DB_PASSWORD=mpm-demo-db-password
# Upstream MPM commit to build (a full 40-character commit hash)
MPM_REF=d6632495586a9dc5c4f71decdec33f5f960701b8
```

- [ ] **Step 4: Validate the compose file**

Run: `docker compose --profile mpm config -q && docker compose config --services`
Expected: no error from the first command; the second lists the original services only (no `mpm-*`), proving the profile keeps them out of the default stack.

- [ ] **Step 5: Boot the MPM part and watch it become healthy**

Run: `docker compose --profile mpm up --build -d mpm-backend mpm-queue-worker mpm-scheduler mpm-frontend` then poll `docker compose ps` until `mpm-backend` is `healthy` (up to about 10 minutes the first time).
Expected: `curl -s -o /dev/null -w "%{http_code}" localhost:8000/up` prints `200`, and `curl -s -o /dev/null -w "%{http_code}" localhost:8001` prints `200`. If the backend stays unhealthy, read `docker compose logs mpm-backend | tail -60` and report the actual error.
(If your other MPM stack from `C:\hackathons\en-access\micropowermanager` is running on ports 8000/8001, stop it first: `docker compose down` in that folder.)

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example integrations/micropowermanager/docker/backend.env
git commit -m "feat(mpm): compose profile that builds and runs MPM with the plugin" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `bootstrap.py`: company, owner and API key

**Files:**
- Create: `integrations/micropowermanager/docker/bootstrap.py`
- Create: `integrations/micropowermanager/tests/test_bootstrap.py`

**Interfaces:**
- Produces: `bootstrap.run(env: Mapping[str, str], sleep=time.sleep, clock=time.monotonic) -> int` (0 = success). Reads `MPM_URL` (default `http://mpm-backend`), `MPM_OWNER_EMAIL`, `MPM_OWNER_PASSWORD`, `MPM_KEY_FILE` (default `/shared/mpm_api_key`), `MPM_READY_TIMEOUT_S` (default `900`), `MPM_PLUGIN_ID` (default `33`). Writes the plaintext API key (one line, no newline needed) to `MPM_KEY_FILE`. Used by Task 6.
- The MPM API it relies on (verified against a running MPM on 2026-10-15): `GET /up` -> 200 when ready; `POST /api/companies` (body: `name`, `address`, `phone`, `email`, `usage_type: "mini-grid"`, `plugins: [{"id": 33}]`, `user: {name, email, password}`) -> 201, or 422 when the company email already exists; `POST /api/auth/login` (`email`, `password`) -> 200 with `access_token`; `POST /api/api-keys` (Bearer token, `{"name": ...}`) -> 201 with `data.token`; `POST /api/libre-solar-bms/ingest/events` with `{}` -> 401 for a bad key, 422 for a valid key with an empty body.

- [ ] **Step 1: Write the failing tests**

Create `integrations/micropowermanager/tests/test_bootstrap.py`:
```python
"""bootstrap.py against a tiny fake MPM (no Docker)."""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "docker"))
import bootstrap  # noqa: E402


class FakeMpm:
    def __init__(self):
        self.ready = True
        self.company_exists = False
        self.valid_keys = set()
        self.calls = []
        self.next_key = "KEY-1"

        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, body=None):
                data = json.dumps(body or {}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.calls.append(("GET", self.path))
                self._reply(200 if fake.ready else 503)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.calls.append(("POST", self.path))
                auth = self.headers.get("Authorization", "")
                if self.path == "/api/companies":
                    if fake.company_exists:
                        return self._reply(422, {"message": "The email has already been taken."})
                    fake.company_exists = True
                    return self._reply(201, {"company": {"id": 5}})
                if self.path == "/api/auth/login":
                    if not fake.company_exists:
                        return self._reply(401)
                    return self._reply(200, {"access_token": "JWT"})
                if self.path == "/api/api-keys":
                    if auth != "Bearer JWT":
                        return self._reply(401)
                    key = fake.next_key
                    fake.valid_keys.add(key)
                    return self._reply(201, {"data": {"id": 1, "token": key}})
                if self.path == "/api/libre-solar-bms/ingest/events":
                    token = auth.removeprefix("Bearer ")
                    return self._reply(422 if token in fake.valid_keys else 401)
                self._reply(404)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def posts(self, path):
        return [c for c in self.calls if c == ("POST", path)]


@pytest.fixture
def mpm():
    fake = FakeMpm()
    yield fake
    fake.server.shutdown()


def env(mpm, tmp_path, **extra):
    return {"MPM_URL": mpm.url, "MPM_OWNER_EMAIL": "owner@example.com", "MPM_OWNER_PASSWORD": "pw-12345",
            "MPM_KEY_FILE": str(tmp_path / "key"), "MPM_READY_TIMEOUT_S": "5", **extra}


def run(mpm, tmp_path, **extra):
    return bootstrap.run(env(mpm, tmp_path, **extra), sleep=lambda s: None)


def test_first_run_registers_signs_in_and_stores_the_key(mpm, tmp_path):
    assert run(mpm, tmp_path) == 0
    assert (tmp_path / "key").read_text().strip() == "KEY-1"
    assert len(mpm.posts("/api/companies")) == 1 and len(mpm.posts("/api/api-keys")) == 1


def test_rerun_with_a_working_key_changes_nothing(mpm, tmp_path):
    run(mpm, tmp_path)
    before = list(mpm.calls)
    assert run(mpm, tmp_path) == 0
    new = mpm.calls[len(before):]
    assert ("POST", "/api/companies") not in new and ("POST", "/api/api-keys") not in new
    assert (tmp_path / "key").read_text().strip() == "KEY-1"


def test_a_stale_key_is_replaced(mpm, tmp_path):
    run(mpm, tmp_path)
    mpm.valid_keys.clear()                       # MPM database was reset, the key file survived
    mpm.company_exists = False
    mpm.next_key = "KEY-2"
    assert run(mpm, tmp_path) == 0
    assert (tmp_path / "key").read_text().strip() == "KEY-2"


def test_existing_company_is_not_an_error_and_a_missing_key_file_is_recreated(mpm, tmp_path):
    mpm.company_exists = True
    assert run(mpm, tmp_path) == 0
    assert (tmp_path / "key").read_text().strip() == "KEY-1"


def test_backend_that_never_gets_ready_fails_with_a_nonzero_exit(mpm, tmp_path, capsys):
    mpm.ready = False
    ticks = iter(range(0, 10_000, 3))
    code = bootstrap.run(env(mpm, tmp_path), sleep=lambda s: None, clock=lambda: next(ticks))
    assert code == 1
    assert "not ready" in capsys.readouterr().err
    assert not (tmp_path / "key").exists()


def test_wrong_owner_password_after_registration_fails_clearly(mpm, tmp_path, capsys):
    mpm.company_exists = True
    original = mpm.server.RequestHandlerClass.do_POST

    def deny_login(handler):
        if handler.path == "/api/auth/login":
            handler.send_response(401)
            handler.send_header("Content-Length", "2")
            handler.end_headers()
            handler.wfile.write(b"{}")
            return
        original(handler)

    mpm.server.RequestHandlerClass.do_POST = deny_login
    assert run(mpm, tmp_path) == 1
    assert "sign in" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify it fails**

Run (repo root): `uv run --no-project --with pytest python -m pytest integrations/micropowermanager/tests/test_bootstrap.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'bootstrap'`.

- [ ] **Step 3: Implement `integrations/micropowermanager/docker/bootstrap.py`**

```python
"""Creates a company, an owner and an API key in a fresh MPM, once, and hands the key to the ingestor.

Runs as the one-shot `mpm-bootstrap` compose service. Standard library only. Safe to run again:
it reuses an existing company and keeps a stored key that still works.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping

REGISTER_TIMEOUT_S = 600          # registration creates the company database and migrates it: minutes


def call(method: str, url: str, body: dict | None = None, token: str | None = None,
         timeout: float = 30) -> tuple[int | None, dict]:
    """(HTTP status or None if unreachable, parsed JSON body or {})."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Accept": "application/json",
        **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, {}
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}


def fail(message: str) -> int:
    print(f"mpm-bootstrap: {message}", file=sys.stderr)
    return 1


def key_works(base: str, key: str) -> bool:
    """An empty ingest request is 422 for a valid key and 401 for an unknown one."""
    status, _ = call("POST", f"{base}/api/libre-solar-bms/ingest/events", {}, token=key)
    return status == 422


def run(env: Mapping[str, str], sleep=time.sleep, clock=time.monotonic) -> int:
    base = env.get("MPM_URL", "http://mpm-backend").rstrip("/")
    email = env.get("MPM_OWNER_EMAIL", "owner@example.com")
    password = env.get("MPM_OWNER_PASSWORD", "demo-password-1")
    key_file = env.get("MPM_KEY_FILE", "/shared/mpm_api_key")
    ready_timeout = float(env.get("MPM_READY_TIMEOUT_S", "900"))
    plugin_id = int(env.get("MPM_PLUGIN_ID", "33"))

    deadline = clock() + ready_timeout
    while True:
        status, _ = call("GET", f"{base}/up")
        if status == 200:
            break
        if clock() >= deadline:
            return fail(f"MPM at {base} is not ready after {ready_timeout:.0f} s")
        sleep(5)

    try:
        stored = open(key_file, encoding="utf-8").read().strip()
    except OSError:
        stored = ""
    if stored and key_works(base, stored):
        print("mpm-bootstrap: stored API key still works; nothing to do")
        return 0

    status, _ = call("POST", f"{base}/api/companies", {
        "name": "Haske Demo", "address": "Kigali", "phone": "+250788000000", "email": email,
        "usage_type": "mini-grid", "plugins": [{"id": plugin_id}],
        "user": {"name": "Owner", "email": email, "password": password}}, timeout=REGISTER_TIMEOUT_S)
    if status == 201:
        print("mpm-bootstrap: company and owner created")
    elif status == 422:
        print("mpm-bootstrap: company already exists; signing in")
    else:
        return fail(f"company registration failed (HTTP {status})")

    status, body = call("POST", f"{base}/api/auth/login", {"email": email, "password": password})
    if status != 200 or "access_token" not in body:
        return fail(f"could not sign in as {email} (HTTP {status}); check MPM_OWNER_EMAIL / MPM_OWNER_PASSWORD")

    status, body = call("POST", f"{base}/api/api-keys", {"name": "gateway"}, token=body["access_token"])
    if status != 201 or "token" not in body.get("data", {}):
        return fail(f"could not create an API key (HTTP {status})")

    os.makedirs(os.path.dirname(key_file) or ".", exist_ok=True)
    with open(key_file, "w", encoding="utf-8") as handle:
        handle.write(body["data"]["token"])
    print(f"mpm-bootstrap: API key written to {key_file}")
    return 0


if __name__ == "__main__":
    sys.exit(run(os.environ))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --no-project --with pytest python -m pytest integrations/micropowermanager/tests/test_bootstrap.py -q`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add integrations/micropowermanager/docker/bootstrap.py integrations/micropowermanager/tests/test_bootstrap.py
git commit -m "feat(mpm): bootstrap script creates the company, owner and API key" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `mpm-ingestor` reads the key from a file

**Files:**
- Modify: `services/mpm-ingestor/sender.py`
- Modify: `services/mpm-ingestor/main.py`
- Modify: `services/mpm-ingestor/tests/test_ingestor.py` (append tests)
- Modify: `services/mpm-ingestor/readme.md` (document `MPM_API_KEY_FILE`)

**Interfaces:**
- Produces: `Sender(outbox, base_url, api_key, transport=http_post, key_file: str | None = None)`; `Sender.current_key() -> str` returns `api_key` if set, else the stripped contents of `key_file`, else `""`. `run_once` uses it on every call, so a key that appears later is picked up without a restart. Env var `MPM_API_KEY_FILE` (read in `main.py`).

- [ ] **Step 1: Write the failing tests** (append to `services/mpm-ingestor/tests/test_ingestor.py`)

```python
def make_keyfile(tmp_path, key_file, api_key=""):
    clock = Clock()
    outbox = Outbox(str(tmp_path / "o.db"), max_rows=5, clock=clock)
    calls = []
    sender = Sender(outbox, "http://mpm:8000", api_key,
                    lambda url, key, body: calls.append(key) or 200, key_file=str(key_file))
    return outbox, sender, calls


def test_key_file_that_appears_later_is_picked_up_without_a_restart(tmp_path):
    keyfile = tmp_path / "key"
    outbox, sender, calls = make_keyfile(tmp_path, keyfile)
    outbox.add_event("a", {"i": 1})
    assert not sender.run_once() and calls == []              # no key yet: queued, nothing sent
    assert outbox.depth()["event"] == 1
    keyfile.write_text("FILEKEY\n")
    assert sender.run_once() and calls == ["FILEKEY"]         # newline stripped
    assert outbox.depth()["event"] == 0


def test_env_key_wins_over_the_key_file(tmp_path):
    keyfile = tmp_path / "key"
    keyfile.write_text("FILEKEY")
    outbox, sender, calls = make_keyfile(tmp_path, keyfile, api_key="ENVKEY")
    outbox.add_event("a", {"i": 1})
    sender.run_once()
    assert calls == ["ENVKEY"]


def test_an_empty_key_file_means_no_key(tmp_path):
    keyfile = tmp_path / "key"
    keyfile.write_text("  \n")
    outbox, sender, calls = make_keyfile(tmp_path, keyfile)
    outbox.add_event("a", {"i": 1})
    assert not sender.run_once() and calls == [] and sender.current_key() == ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd services/mpm-ingestor && uv run pytest tests/test_ingestor.py -q`
Expected: the 3 new tests FAIL (`unexpected keyword argument 'key_file'`).

- [ ] **Step 3: Implement**

In `sender.py` replace `Sender.__init__` and the first lines of `run_once` and `_deliver`:
```python
    def __init__(self, outbox: Outbox, base_url: str, api_key: str, transport: Transport = http_post,
                 key_file: str | None = None):
        self.outbox, self.base_url, self.api_key, self.transport = outbox, base_url.rstrip("/"), api_key, transport
        self.key_file = key_file
        self.last_success: float | None = None
        self.last_status: int | None = None

    def current_key(self) -> str:
        """The key from the environment, else from `key_file` (written later by mpm-bootstrap), else ''."""
        if self.api_key:
            return self.api_key
        if self.key_file:
            try:
                with open(self.key_file, encoding="utf-8") as handle:
                    return handle.read().strip()
            except OSError:
                return ""
        return ""

    def run_once(self) -> bool:
        """Try everything that is due. Returns True if any row was acknowledged or dropped."""
        if not self.current_key():
            return False
```
and in `_deliver` change the transport call to `self.transport(self.base_url + path, self.current_key(), body)`.

In `main.py`: after `MPM_API_KEY = ...` add `MPM_API_KEY_FILE = os.getenv("MPM_API_KEY_FILE", "")`; change `sender = Sender(outbox, MPM_BASE_URL, MPM_API_KEY)` to `sender = Sender(outbox, MPM_BASE_URL, MPM_API_KEY, key_file=MPM_API_KEY_FILE or None)`; change `"mpm_configured": bool(MPM_API_KEY),` to `"mpm_configured": bool(sender.current_key()),`; change the startup warning condition to `if not sender.current_key():` with text `"No MPM API key yet (MPM_API_KEY or MPM_API_KEY_FILE): messages are queued but NOT delivered until one appears."`.

- [ ] **Step 4: Run the full service suite**

Run: `cd services/mpm-ingestor && uv run pytest tests -q`
Expected: all pass (existing tests unchanged, plus the 3 new).

- [ ] **Step 5: Document**

In `services/mpm-ingestor/readme.md`, where `MPM_API_KEY` is described, add: `MPM_API_KEY_FILE`: path of a file holding the key (read on every delivery attempt, so it may appear after start). `MPM_API_KEY` wins when both are set. The compose `mpm` profile uses `/shared/mpm_api_key`, written by `mpm-bootstrap`.

- [ ] **Step 6: Commit**

```bash
git add services/mpm-ingestor
git commit -m "feat(ingestor): read the MPM API key from a file that may appear after start" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Wire bootstrap and the ingestor in compose; end-to-end check; docs

**Files:**
- Modify: `docker-compose.yml` (service `mpm-bootstrap`; `mpm-ingestor` env and volume)
- Modify: `integrations/micropowermanager/README.md`, `README.md`, `TODO.txt`

**Interfaces:**
- Consumes: Task 3 services and volume `mpm-shared`; Task 4 `bootstrap.py`; Task 5 `MPM_API_KEY_FILE`.

- [ ] **Step 1: Add `mpm-bootstrap` and wire `mpm-ingestor`**

Add the service:
```yaml
  mpm-bootstrap:
    # One-shot: creates the company, the owner and an API key, and leaves the key in the shared volume.
    profiles: ["mpm"]
    image: python:3.12-slim
    command: ["python", "/bootstrap.py"]
    environment:
      MPM_URL: http://mpm-backend
      MPM_OWNER_EMAIL: ${MPM_OWNER_EMAIL:-owner@example.com}
      MPM_OWNER_PASSWORD: ${MPM_OWNER_PASSWORD:-demo-password-1}
      MPM_KEY_FILE: /shared/mpm_api_key
    volumes:
      - ./integrations/micropowermanager/docker/bootstrap.py:/bootstrap.py:ro
      - mpm-shared:/shared
    depends_on:
      mpm-backend: {condition: service_healthy}
    restart: "no"
```
In the existing `mpm-ingestor` service add under `environment:` `MPM_API_KEY_FILE: /shared/mpm_api_key` and under `volumes:` `- mpm-shared:/shared:ro`. (No `depends_on` on bootstrap: without the profile that service does not exist, and the ingestor already queues until a key appears.)

- [ ] **Step 2: Validate and run the whole thing from clean**

```bash
docker compose --profile mpm config -q
docker compose --profile mpm down -v
docker compose --profile mpm up --build -d
```
Expected: no errors. Then poll (up to about 15 minutes on a cold build) until `docker compose --profile mpm ps -a mpm-bootstrap` shows `Exited (0)`. Read `docker compose --profile mpm logs mpm-bootstrap`; expected lines: `company and owner created`, then `API key written to /shared/mpm_api_key`.

- [ ] **Step 3: Verify the data flows to MPM**

```bash
docker compose --profile mpm logs --since 3m mpm-ingestor | tail -5
curl -s localhost:8012 | python -m json.tool
```
Expected: `mpm_configured: true` and `last_mpm_status: 200`. Then sign in at http://localhost:8001 with `MPM_OWNER_EMAIL` / `MPM_OWNER_PASSWORD` from `.env` (defaults: `owner@example.com` / `demo-password-1`), open Battery Health, Batteries: `haske-bms-001` is listed within about a minute. (Check by API if the browser is awkward: `POST /api/auth/login`, then `GET /api/libre-solar-bms/batteries` with the Bearer token; expect a battery named `haske-bms-001`.)

- [ ] **Step 4: Verify re-run safety**

```bash
docker compose --profile mpm up -d --force-recreate mpm-bootstrap
docker compose --profile mpm logs mpm-bootstrap | tail -3
```
Expected: `stored API key still works; nothing to do`, exit 0, and still exactly one company (`docker compose --profile mpm exec mpm-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "select id,name from micro_power_manager.companies"'` shows no duplicate "Haske Demo").
Then verify the no-profile stack is unchanged: `docker compose down && docker compose config --services` lists no `mpm-*` services.

- [ ] **Step 5: Update the docs**

- `integrations/micropowermanager/README.md`: at the top add a section `## Quick start (one command)` with: `docker compose --profile mpm up --build -d`, the first-build time warning (about 10-20 minutes: PHP, Composer and the Vue build), the URLs (MPM http://localhost:8001, backend 8000), the login defaults and how to change them in `.env`, and that data persists in the `mpm-*` volumes (`docker compose --profile mpm down -v` resets). Rename the existing manual setup heading to `## Manual setup (develop the plugin)` and say the one-command route builds from a pinned upstream commit (`MPM_REF`), while the manual route is for editing the plugin with hot reload.
- `README.md` (root): in "Add MicroPowerManager" replace the first sentence with the one-command route and link to the integration README.
- `TODO.txt`: delete item 2.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml integrations/micropowermanager/README.md README.md TODO.txt
git commit -m "feat(mpm): one-command MPM with the plugin, bootstrap and ingestor key wiring" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review

- **Spec coverage:** pinned upstream clone + overlay in one build (Task 1), frontend (Task 2), services in the root compose under a profile (Task 3), no manual key (Tasks 4-6), mail handling no longer needed (item 4 dropped and MPM registration verified), docs (Task 6). The spec's open points are answered: MPM's own services are expressed as `mpm-*` compose services; the API key is created by `bootstrap.py`; `MPM_BASE_URL` keeps working through the published port 8000.
- **Placeholders:** none. Step 1 of Task 3 deliberately points at the upstream file for the public dev `APP_KEY`/`JWT_SECRET` rather than copying them into this document.
- **Types/names:** `bootstrap.run`, `call`, `key_works`, `Sender.current_key`, `MPM_API_KEY_FILE`, `/shared/mpm_api_key`, volume `mpm-shared`, image `libre-solar-mpm-backend` are used identically everywhere.
- **Known risks:** the first build is long and needs network access to GitHub, Docker Hub, packagist and npm; port clash with a separately running MPM on 8000/8001; `composer install --no-dev` might miss a package that the plugin controller's `Dedoc\Scramble` attribute refers to (attribute classes are not loaded unless reflected, but Task 1 Step 4 and Task 6 Step 3 would surface it).
