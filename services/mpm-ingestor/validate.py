"""Checks `summary` and `event` payloads before they enter the outbox.

Mirrors the rules of the MPM plugin (IngestSummaryRequest / IngestEventsRequest in
integrations/micropowermanager/.../LibreSolarBms/Http/Requests). Why validate here as well:
events are sent to MPM in batches of up to 50, and MPM answers 422 for the WHOLE batch when one
event is invalid, which the sender treats as "drop". One malformed message (an old-format
payload, a typo in a custom publisher) would take 49 good events with it. Checking each message
at the door keeps the bad one out and says why in the log.

Pure functions, no MQTT and no I/O. Each returns None when the payload is fine, otherwise a short
reason that is safe to log.
"""
from __future__ import annotations

from datetime import datetime

HEALTH = ("green", "amber", "red")
EVENT_TYPES = ("fault_raised", "fault_cleared", "device_online", "device_offline")
SEVERITIES = ("critical", "warning", "info")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_date(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _text(payload: dict, key: str, max_len: int, required: bool) -> str | None:
    value = payload.get(key)
    if value is None or value == "":
        return f"{key} is required" if required else None
    if not isinstance(value, str):
        return f"{key} must be a string"
    if len(value) > max_len:
        return f"{key} is longer than {max_len} characters"
    return None


def _number(payload: dict, key: str, low: float | None = None, high: float | None = None) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not _is_number(value):
        return f"{key} must be a number"
    if low is not None and value < low:
        return f"{key} must be >= {low}"
    if high is not None and value > high:
        return f"{key} must be <= {high}"
    return None


def _string_list(payload: dict, key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return f"{key} must be a list of strings"
    return None


def _first(*results: str | None) -> str | None:
    return next((r for r in results if r), None)


def _common(payload, topic_device_id: str | None) -> str | None:
    if not isinstance(payload, dict):
        return "payload is not a JSON object"
    err = _first(_text(payload, "device_id", 100, True),
                 None if _is_date(payload.get("ts")) else "ts is missing or not a date")
    if err:
        return err
    if topic_device_id is not None and payload["device_id"] != topic_device_id:
        # MPM files the data under payload.device_id: a mismatch would land on the wrong battery.
        return f"device_id {payload['device_id']!r} does not match topic id {topic_device_id!r}"
    return None


def check_summary(payload, topic_device_id: str | None = None) -> str | None:
    err = _common(payload, topic_device_id)
    if err:
        return err
    score = payload.get("health_score")
    if score is not None and not (_is_number(score) and float(score).is_integer() and 0 <= score <= 100):
        score_err = "health_score must be an integer between 0 and 100"
    else:
        score_err = None
    return _first(
        _text(payload, "state", 20, False),
        _number(payload, "soc_pct", 0, 100),
        _number(payload, "soh_pct", 0, 100),
        _number(payload, "cycles", 0),
        _number(payload, "pack_voltage_v"),
        _number(payload, "imbalance_v", 0),
        _number(payload, "max_temp_c"),
        None if payload.get("health") in HEALTH else f"health must be one of {', '.join(HEALTH)}",
        score_err,
        _string_list(payload, "health_reasons"),
        _string_list(payload, "active_faults"),
    )


def check_event(payload, topic_device_id: str | None = None) -> str | None:
    err = _common(payload, topic_device_id)
    if err:
        return err
    return _first(
        None if payload.get("type") in EVENT_TYPES else f"type must be one of {', '.join(EVENT_TYPES)}",
        None if payload.get("severity") in SEVERITIES else f"severity must be one of {', '.join(SEVERITIES)}",
        _text(payload, "fault", 50, False),
        _text(payload, "message", 255, True),
        None if payload.get("action") is None or isinstance(payload.get("action"), str)
        else "action must be a string",
    )
