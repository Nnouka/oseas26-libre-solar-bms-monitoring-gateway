"""Ingestor message handling: what reaches the outbox and what is refused. No broker needed."""
import json
from types import SimpleNamespace

from ingestor import Ingestor
from outbox import Outbox
from sender import Sender
from test_validate import event, summary


def make(tmp_path):
    outbox = Outbox(str(tmp_path / "o.db"))
    return Ingestor("localhost", 1883, outbox), outbox     # creating it does not connect


def msg(topic: str, payload) -> SimpleNamespace:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return SimpleNamespace(topic=topic, payload=raw)


def test_valid_summary_and_event_are_queued(tmp_path):
    ing, outbox = make(tmp_path)
    ing._on_message(None, None, msg("bms/haske-bms-001/summary", summary()))
    ing._on_message(None, None, msg("bms/haske-bms-001/event", event()))
    assert outbox.depth() == {"summary": 1, "event": 1}
    assert ing.stats == {"summary": {"accepted": 1, "rejected": 0}, "event": {"accepted": 1, "rejected": 0}}
    assert ing.last_rejected is None


def test_invalid_messages_are_refused_counted_and_explained(tmp_path):
    ing, outbox = make(tmp_path)
    ing._on_message(None, None, msg("bms/haske-bms-001/event", event(type="reason")))
    ing._on_message(None, None, msg("bms/haske-bms-001/summary", summary(health="purple")))
    ing._on_message(None, None, msg("bms/haske-bms-001/event", b"{not json"))
    assert outbox.depth() == {"summary": 0, "event": 0}
    assert ing.stats["event"]["rejected"] == 2 and ing.stats["summary"]["rejected"] == 1
    assert ing.last_rejected.startswith("bms/haske-bms-001/event: payload is not valid JSON")


def test_a_payload_for_another_device_is_refused(tmp_path):
    ing, outbox = make(tmp_path)
    ing._on_message(None, None, msg("bms/haske-bms-001/summary", summary(device_id="haske-bms-002")))
    assert outbox.depth()["summary"] == 0 and "does not match" in ing.last_rejected


def test_a_cleared_retained_message_is_ignored_quietly(tmp_path):
    ing, outbox = make(tmp_path)
    ing._on_message(None, None, msg("bms/haske-bms-001/summary", b""))
    assert outbox.depth()["summary"] == 0 and ing.last_rejected is None


def test_other_topics_are_ignored(tmp_path):
    ing, outbox = make(tmp_path)
    ing._on_message(None, None, msg("bms/haske-bms-001/meas", {"anything": 1}))
    assert outbox.depth() == {"summary": 0, "event": 0}


def test_fault_events_travel_together_in_order(tmp_path):
    ing, outbox = make(tmp_path)
    ing._on_message(None, None, msg("bms/haske-bms-001/event", event()))
    ing._on_message(None, None, msg("bms/haske-bms-001/event", event(type="fault_cleared", severity="info")))
    sent = []
    Sender(outbox, "http://mpm:8000", "KEY", lambda url, key, body: sent.append(body) or 200).run_once()
    assert [e["type"] for e in sent[0]["events"]] == ["fault_raised", "fault_cleared"]


def test_one_bad_event_no_longer_takes_a_whole_batch_down(tmp_path):
    """MPM answers 422 for a batch with one invalid event, and the sender drops 422s."""
    ing, outbox = make(tmp_path)
    for i in range(3):
        ing._on_message(None, None, msg("bms/haske-bms-001/event", event(message=f"fault {i}")))
    ing._on_message(None, None, msg("bms/haske-bms-001/event", event(severity="fatal")))
    sent = []
    sender = Sender(outbox, "http://mpm:8000", "KEY", lambda url, key, body: sent.append(body) or 200)
    assert sender.run_once()
    assert [e["message"] for e in sent[0]["events"]] == ["fault 0", "fault 1", "fault 2"]
