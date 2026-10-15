"""The payload checks mirror MPM's IngestSummaryRequest / IngestEventsRequest."""
from validate import check_event, check_summary


def summary(**kw) -> dict:
    base = {"schema": 1, "device_id": "haske-bms-001", "ts": "2026-10-09T14:32:05Z",
            "last_seen": "2026-10-09T14:32:05Z", "state": "NORMAL", "soc_pct": 71.5,
            "pack_voltage_v": 26.41, "soh_pct": 99.9, "soh_method": "cycle_model", "cycles": 1.2,
            "imbalance_v": 0.012, "max_temp_c": 34.0, "health": "green", "health_score": 100,
            "health_reasons": [], "active_faults": []}
    return {**base, **kw}


def event(**kw) -> dict:
    base = {"schema": 1, "device_id": "haske-bms-001", "ts": "2026-10-09T14:32:05Z",
            "type": "fault_raised", "fault": "CHG_OVERTEMP", "severity": "critical",
            "message": "Battery too hot to charge",
            "action": "Check ventilation and fans; shade the enclosure; charging resumes when it cools."}
    return {**base, **kw}


def test_the_documented_examples_pass():
    assert check_summary(summary(), "haske-bms-001") is None
    assert check_event(event(), "haske-bms-001") is None


def test_device_events_have_no_fault_and_may_have_an_empty_action():
    online = event(type="device_online", fault=None, severity="info", action="")
    assert check_event(online) is None


def test_a_summary_without_health_or_with_a_wrong_colour_is_refused():
    no_health = summary(); del no_health["health"]
    assert "health" in check_summary(no_health)
    assert "health" in check_summary(summary(health="purple"))


def test_summary_numbers_are_range_checked():
    assert "soc_pct" in check_summary(summary(soc_pct=101))
    assert "soh_pct" in check_summary(summary(soh_pct=-1))
    assert "cycles" in check_summary(summary(cycles=-0.1))
    assert "imbalance_v" in check_summary(summary(imbalance_v=-0.01))
    assert "health_score" in check_summary(summary(health_score=50.5))
    assert "soc_pct" in check_summary(summary(soc_pct="71"))           # a string is not a number
    assert "soc_pct" in check_summary(summary(soc_pct=True))           # neither is a bool


def test_summary_lists_must_hold_strings():
    assert "active_faults" in check_summary(summary(active_faults=[1]))
    assert "health_reasons" in check_summary(summary(health_reasons="hot"))


def test_nullable_fields_may_be_missing_or_null():
    assert check_summary({"device_id": "d", "ts": "2026-10-09T14:32:05Z", "health": "amber"}) is None
    assert check_summary(summary(state=None, soc_pct=None, health_score=None)) is None


def test_event_enums_and_required_text_are_checked():
    assert "type" in check_event(event(type="reason"))
    assert "severity" in check_event(event(severity="fatal"))
    assert "message" in check_event(event(message=""))
    assert "message" in check_event(event(message="x" * 256))
    assert "fault" in check_event(event(fault="X" * 51))


def test_the_old_compact_payload_is_not_a_valid_event():
    legacy = {"schema": 1, "device_id": "haske-bms-001", "ts": "2026-10-09T14:32:05Z", "reason": "first",
              "state": "NORMAL", "soc_pct": 60.0, "pack_voltage_v": 26.1, "pack_current_a": 3.0,
              "imbalance_mv": 10.0, "max_temp_c": 27.0, "errors": []}
    assert "type" in check_event(legacy)


def test_ts_and_device_id_are_required_and_must_match_the_topic():
    assert "ts" in check_summary(summary(ts="yesterday"))
    assert "ts" in check_event(event(ts=None))
    assert "device_id" in check_summary(summary(device_id=""))
    assert "does not match" in check_summary(summary(device_id="other"), "haske-bms-001")
    assert "does not match" in check_event(event(device_id="other"), "haske-bms-001")


def test_payload_must_be_an_object():
    assert "object" in check_summary([1, 2])
    assert "object" in check_event("online")
