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
        self.login_denied = False
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
                self.rfile.read(length)
                fake.calls.append(("POST", self.path))
                auth = self.headers.get("Authorization", "")
                if self.path == "/api/companies":
                    if fake.company_exists:
                        return self._reply(422, {"message": "The email has already been taken."})
                    fake.company_exists = True
                    return self._reply(201, {"company": {"id": 5}})
                if self.path == "/api/auth/login":
                    if fake.login_denied or not fake.company_exists:
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
    assert mpm.calls.count(("POST", "/api/companies")) == 1 and mpm.calls.count(("POST", "/api/api-keys")) == 1


def test_rerun_with_a_working_key_changes_nothing(mpm, tmp_path):
    run(mpm, tmp_path)
    before = len(mpm.calls)
    assert run(mpm, tmp_path) == 0
    new = mpm.calls[before:]
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
    mpm.login_denied = True
    assert run(mpm, tmp_path) == 1
    assert "sign in" in capsys.readouterr().err
