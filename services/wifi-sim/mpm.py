"""Decision-worthy summaries for MPM (topic bms/<id>/mpm). No MQTT in here: pure logic, easy to test."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from schema import ErrorName

SCHEMA_VERSION = 1
Reason = Literal["first", "state_change", "errors_changed", "imbalance_high", "imbalance_ok",
                 "soc_step", "heartbeat"]


class MpmEvent(BaseModel):
    """Payload of bms/<id>/mpm. Small on purpose: no per-cell arrays."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(alias="schema")
    device_id: str
    ts: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    reason: Reason
    state: Literal["OFF", "CHG", "DIS", "NORMAL", "SHUTDOWN"]
    soc_pct: float = Field(ge=0, le=100)
    pack_voltage_v: float
    pack_current_a: float
    imbalance_mv: float
    max_temp_c: float
    errors: list[ErrorName]


class MpmFilter:
    """Looks at EVERY measurement, lets through only the ones worth a decision."""

    def __init__(self, soc_step_pct: float = 5.0, imbalance_mv: float = 50.0, heartbeat_s: float = 300.0):
        self.soc_step_pct, self.imbalance_mv, self.heartbeat_s = soc_step_pct, imbalance_mv, heartbeat_s
        self._last: dict | None = None      # what MPM was last told
        self._last_at = 0.0

    def check(self, meas: dict, now: float) -> dict | None:
        """Return the event to publish (a dict that obeys MpmEvent), or None. `now` is a monotonic clock."""
        v = meas["cell_voltages_v"]
        imbalance = round((max(v) - min(v)) * 1000.0, 1)
        errors = sorted(meas["errors"])
        high = imbalance >= self.imbalance_mv
        last = self._last

        if last is None:
            reason = "first"
        elif meas["state"] != last["state"]:
            reason = "state_change"
        elif errors != last["errors"]:
            reason = "errors_changed"
        elif high != last["high"]:
            reason = "imbalance_high" if high else "imbalance_ok"
        elif abs(meas["soc_pct"] - last["soc_pct"]) >= self.soc_step_pct:
            reason = "soc_step"            # measured against what MPM last heard, so no flapping at a boundary
        elif now - self._last_at >= self.heartbeat_s:
            reason = "heartbeat"           # proof of life, and MPM's "last seen"
        else:
            return None

        self._last = {"state": meas["state"], "errors": errors, "high": high, "soc_pct": meas["soc_pct"]}
        self._last_at = now
        event = MpmEvent(
            schema=SCHEMA_VERSION, device_id=meas["device_id"], ts=meas["ts"], reason=reason,
            state=meas["state"], soc_pct=meas["soc_pct"], pack_voltage_v=meas["pack_voltage_v"],
            pack_current_a=meas["pack_current_a"], imbalance_mv=imbalance,
            max_temp_c=max([*meas["cell_temps_c"], meas["mosfet_temp_c"]]), errors=errors,
        )
        return event.model_dump(by_alias=True)
