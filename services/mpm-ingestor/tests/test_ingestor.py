import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import sqlite3
from outbox import Outbox, backoff_s, MAX_BACKOFF_S
from sender import Sender


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def make(tmp_path, statuses):
    clock = Clock()
    outbox = Outbox(str(tmp_path / "o.db"), max_rows=5, clock=clock)
    calls = []

    def transport(url, key, body):
        calls.append((url, key, body))
        return statuses.pop(0) if statuses else 200

    return outbox, Sender(outbox, "http://mpm:8000/", "KEY", transport), calls, clock


def test_summary_is_coalesced_per_device(tmp_path):
    outbox, sender, calls, _ = make(tmp_path, [])
    outbox.add_summary("a", {"n": 1}); outbox.add_summary("a", {"n": 2}); outbox.add_summary("b", {"n": 3})
    assert outbox.depth()["summary"] == 2
    assert sender.run_once()
    assert sorted(c[2]["n"] for c in calls) == [2, 3]
    assert outbox.depth() == {"summary": 0, "event": 0}


def test_events_are_batched_in_order_and_use_bearer_key(tmp_path):
    outbox, sender, calls, _ = make(tmp_path, [])
    for i in range(3):
        outbox.add_event("a", {"i": i})
    sender.run_once()
    url, key, body = calls[0]
    assert url == "http://mpm:8000/api/libre-solar-bms/ingest/events" and key == "KEY"
    assert [e["i"] for e in body["events"]] == [0, 1, 2]


def test_outage_keeps_rows_and_backs_off_then_recovers(tmp_path):
    outbox, sender, calls, clock = make(tmp_path, [None, 503, 200])
    outbox.add_event("a", {"i": 1})
    assert not sender.run_once()                 # unreachable
    assert outbox.depth()["event"] == 1
    assert not sender.run_once()                 # not due yet: no second call during backoff
    assert len(calls) == 1
    clock.t += backoff_s(1) + 0.1
    assert not sender.run_once()                 # 503
    clock.t += backoff_s(2) + 0.1
    assert sender.run_once()                     # 200
    assert outbox.depth()["event"] == 0


def test_rejected_data_is_dropped_but_auth_errors_are_kept(tmp_path):
    outbox, sender, _, clock = make(tmp_path, [422, 401])
    outbox.add_event("a", {"bad": True})
    assert sender.run_once() and outbox.depth()["event"] == 0     # 422 dropped
    outbox.add_event("a", {"ok": True})
    assert not sender.run_once() and outbox.depth()["event"] == 1  # 401 kept for retry


def test_no_api_key_means_nothing_is_sent(tmp_path):
    outbox, sender, calls, _ = make(tmp_path, [])
    sender.api_key = ""
    outbox.add_event("a", {})
    assert not sender.run_once() and not calls and outbox.depth()["event"] == 1


def test_queue_survives_restart(tmp_path):
    path = str(tmp_path / "o.db")
    Outbox(path).add_event("a", {"i": 1})
    assert Outbox(path).depth()["event"] == 1


def test_event_trim_drops_oldest_when_full(tmp_path):
    outbox, _, _, _ = make(tmp_path, [])
    for i in range(8):
        outbox.add_event("a", {"i": i})
    kept = [p["i"] for _, p in outbox.due("event", 100)]
    assert kept == [3, 4, 5, 6, 7]


def test_backoff_is_capped():
    assert backoff_s(1) == 2 and backoff_s(50) == MAX_BACKOFF_S
