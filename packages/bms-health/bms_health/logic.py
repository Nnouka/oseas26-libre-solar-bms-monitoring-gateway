"""Pure bridge logic: derived metrics, health score, fault events.

No MQTT code in here, so it can be unit-tested without a broker.
Input is the raw `meas` payload (see docs/mqtt-schema.md); output is the
`summary` payload plus `event` payloads.
"""
from __future__ import annotations

from datetime import datetime

SCHEMA_VERSION = 1

# name -> (severity, plain-language message, recommended action)
FAULTS: dict[str, tuple[str, str, str]] = {
    "CELL_UNDERVOLTAGE": ("critical", "A cell is over-discharged",
                          "Cut the load and recharge as soon as possible; inspect the weakest cell."),
    "CELL_OVERVOLTAGE": ("critical", "A cell is overcharged",
                         "Check the charge controller voltage settings; stop charging until it clears."),
    "SHORT_CIRCUIT": ("critical", "Short circuit detected on the battery output",
                      "Disconnect the load and inspect wiring before switching back on."),
    "DIS_OVERCURRENT": ("critical", "Discharge current is too high",
                        "Reduce the connected load; the system is undersized for this demand."),
    "CHG_OVERCURRENT": ("critical", "Charge current is too high",
                        "Check the solar charge controller current limit."),
    "OPEN_WIRE": ("critical", "A cell sense wire is disconnected",
                  "Send a technician to check the BMS cell wiring harness."),
    "DIS_UNDERTEMP": ("warning", "Battery too cold to discharge",
                      "Check the enclosure; discharge resumes automatically when it warms up."),
    "DIS_OVERTEMP": ("critical", "Battery too hot to discharge",
                     "Check ventilation and cooling; reduce the load until it cools."),
    "CHG_UNDERTEMP": ("warning", "Battery too cold to charge",
                      "Charging resumes automatically when it warms up."),
    "CHG_OVERTEMP": ("critical", "Battery too hot to charge",
                     "Check ventilation and fans; shade the enclosure; charging resumes when it cools."),
    "INT_OVERTEMP": ("warning", "BMS electronics are overheating",
                     "Check enclosure ventilation around the BMS board."),
    "CELL_FAILURE": ("critical", "Cell voltages are far apart (possible failing cell)",
                     "Schedule an inspection: one cell may be weak or damaged."),
    "DIS_OFF": ("warning", "Discharge switch is off when it should be on",
                "Inspect the BMS; the discharge MOSFET may have failed."),
    "CHG_OFF": ("warning", "Charge switch is off when it should be on",
                "Inspect the BMS; the charge MOSFET may have failed."),
    "FET_OVERTEMP": ("critical", "BMS power switches are overheating",
                     "Reduce current and check cooling around the BMS."),
}

# Rough LFP end-of-life model: ~80% health after 3000 equivalent full cycles.
SOH_LOSS_PER_CYCLE_PCT = 20.0 / 3000.0
MAX_GAP_S = 600           # ignore gaps longer than this when integrating current
IMBALANCE_AMBER_V = 0.10
IMBALANCE_RED_V = 0.20


def fault_info(name: str) -> tuple[str, str, str]:
    return FAULTS.get(name, ("warning", f"Fault {name}", "Check the BMS."))


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def assess_health(meas: dict, faults: set[str], soh_pct: float) -> tuple[str, int, list[str]]:
    """Return (green|amber|red, score 0-100, reasons)."""
    level, penalty, reasons = 0, 0, []   # level: 0 green, 1 amber, 2 red

    def flag(lvl: int, pen: int, text: str) -> None:
        nonlocal level, penalty
        level, penalty = max(level, lvl), penalty + pen
        reasons.append(text)

    for name in sorted(faults):
        sev, msg, _ = fault_info(name)
        if sev == "critical":
            flag(2, 40, msg)
        else:
            flag(1, 15, msg)

    temps = meas.get("cell_temps_c") or []
    max_t = max(temps) if temps else None
    if max_t is not None:
        if max_t >= 45:
            flag(2, 30, f"Battery very hot ({max_t:.0f} °C)")
        elif max_t >= 40:
            flag(1, 10, f"Battery warm ({max_t:.0f} °C)")

    volts = meas.get("cell_voltages_v") or []
    soc = meas.get("soc_pct")
    # LFP voltage curve is steep near empty/full, so imbalance is only judged mid-range.
    if len(volts) > 1 and soc is not None and 10 <= soc <= 90:
        imb = max(volts) - min(volts)
        if imb >= IMBALANCE_RED_V:
            flag(2, 30, f"Cells badly unbalanced ({imb * 1000:.0f} mV)")
        elif imb >= IMBALANCE_AMBER_V:
            flag(1, 10, f"Cells drifting apart ({imb * 1000:.0f} mV)")

    if soc is not None and soc < 15:
        flag(1, 10, f"Battery nearly empty ({soc:.0f}%)")

    if soh_pct < 80:
        flag(2, 20, f"Battery worn out (health {soh_pct:.0f}%)")
    elif soh_pct < 90:
        flag(1, 5, f"Battery ageing (health {soh_pct:.0f}%)")

    color = ("green", "amber", "red")[level]
    return color, max(0, 100 - penalty), reasons


class DeviceTracker:
    """Keeps per-device state between `meas` messages."""

    def __init__(self, device_id: str, time_scale: float = 1.0, capacity_ah: float = 100.0):
        self.device_id = device_id
        self.time_scale = time_scale      # >1 only when fed by an accelerated simulator
        self.capacity_ah = capacity_ah
        self.ah_throughput = 0.0
        self.last_ts: datetime | None = None
        self.last_meas: dict | None = None
        self.active_faults: set[str] = set()
        self.health: str | None = None
        self.online: bool | None = None
        self.last_summary_mono = 0.0      # set by the bridge

    # -- derived values ----------------------------------------------------
    @property
    def cycles(self) -> float:
        return self.ah_throughput / (2.0 * self.capacity_ah)

    @property
    def soh_pct(self) -> float:
        return max(0.0, 100.0 - self.cycles * SOH_LOSS_PER_CYCLE_PCT)

    def set_info(self, info: dict) -> None:
        cap = info.get("capacity_ah")
        if isinstance(cap, (int, float)) and cap > 0:
            self.capacity_ah = float(cap)

    # -- message handlers --------------------------------------------------
    def on_meas(self, meas: dict) -> tuple[list[dict], bool]:
        """Process a measurement. Returns (events, publish_summary_now)."""
        ts = parse_ts(meas["ts"])
        if self.last_ts is not None:
            dt = (ts - self.last_ts).total_seconds()
            if 0 < dt <= MAX_GAP_S:
                self.ah_throughput += abs(meas.get("pack_current_a", 0.0)) * dt * self.time_scale / 3600.0
        self.last_ts, self.last_meas = ts, meas

        now_faults = set(meas.get("errors") or [])
        events = []
        for name in sorted(now_faults - self.active_faults):
            events.append(self._fault_event(meas["ts"], "fault_raised", name))
        for name in sorted(self.active_faults - now_faults):
            events.append(self._fault_event(meas["ts"], "fault_cleared", name))
        faults_changed = now_faults != self.active_faults
        self.active_faults = now_faults

        color, _, _ = assess_health(meas, now_faults, self.soh_pct)
        health_changed = color != self.health
        self.health = color
        return events, faults_changed or health_changed

    def on_status(self, payload: str, ts_iso: str) -> dict | None:
        online = payload.strip().lower() == "online"
        prev, self.online = self.online, online
        if prev == online or (prev is None and online):
            return None
        return {
            "schema": SCHEMA_VERSION, "device_id": self.device_id, "ts": ts_iso,
            "type": "device_online" if online else "device_offline",
            "fault": None,
            "severity": "info" if online else "warning",
            "message": "Battery monitor is back online" if online else "Lost contact with the battery monitor",
            "action": "" if online else "Check power and network connection at the site.",
        }

    def _fault_event(self, ts: str, kind: str, name: str) -> dict:
        sev, msg, action = fault_info(name)
        return {
            "schema": SCHEMA_VERSION, "device_id": self.device_id, "ts": ts,
            "type": kind, "fault": name,
            "severity": sev if kind == "fault_raised" else "info",
            "message": msg if kind == "fault_raised" else f"Cleared: {msg}",
            "action": action if kind == "fault_raised" else "",
        }

    # -- summary -----------------------------------------------------------
    def summary(self) -> dict | None:
        m = self.last_meas
        if m is None:
            return None
        volts = m.get("cell_voltages_v") or [0.0]
        temps = m.get("cell_temps_c") or [0.0]
        color, score, reasons = assess_health(m, self.active_faults, self.soh_pct)
        return {
            "schema": SCHEMA_VERSION,
            "device_id": self.device_id,
            "ts": m["ts"],
            "last_seen": m["ts"],
            "state": m.get("state"),
            "soc_pct": m.get("soc_pct"),
            "soh_pct": round(self.soh_pct, 1),
            "soh_method": "cycle_model",
            "cycles": round(self.cycles, 2),
            "pack_voltage_v": m.get("pack_voltage_v"),
            "imbalance_v": round(max(volts) - min(volts), 3),
            "max_temp_c": max(temps),
            "health": color,
            "health_score": score,
            "health_reasons": reasons,
            "active_faults": sorted(self.active_faults),
        }
