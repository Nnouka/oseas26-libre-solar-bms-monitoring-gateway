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
        with open(key_file, encoding="utf-8") as handle:
            stored = handle.read().strip()
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
