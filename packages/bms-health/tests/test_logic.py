from datetime import datetime, timedelta, timezone

from bms_health import DeviceTracker, assess_health

T0 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=timezone.utc)


def meas(i, current=0.0, soc=60.0, temps=(28.0, 27.0), volts=None, errors=()):
    return {
        "ts": (T0 + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "state": "NORMAL", "soc_pct": soc, "pack_current_a": current, "pack_voltage_v": 26.5,
        "cell_voltages_v": list(volts or [3.30] * 8), "cell_temps_c": list(temps),
        "errors": list(errors),
    }


def test_cycles_from_current_integration():
    t = DeviceTracker("d", capacity_ah=100)
    for i in range(0, 4 * 3600 + 1, 300):         # 4 h at -50 A = 200 Ah
        t.on_meas(meas(i, current=-50.0))
    assert abs(t.cycles - 1.0) < 0.01
    assert t.soh_pct < 100


def test_time_scale_accelerates_integration():
    t = DeviceTracker("d", time_scale=60, capacity_ah=100)
    for i in range(0, 241, 5):                     # 4 real minutes = 4 simulated hours
        t.on_meas(meas(i, current=-50.0))
    assert abs(t.cycles - 1.0) < 0.05


def test_gap_is_not_integrated():
    t = DeviceTracker("d", capacity_ah=100)
    t.on_meas(meas(0, current=50))
    t.on_meas(meas(5000, current=50))              # long outage: skip
    assert t.ah_throughput == 0


def test_fault_raised_then_cleared_emits_events_once():
    t = DeviceTracker("d")
    ev, now = t.on_meas(meas(0))
    assert ev == []
    ev, now = t.on_meas(meas(5, errors=["CHG_OVERTEMP"]))
    assert [e["type"] for e in ev] == ["fault_raised"] and now
    assert ev[0]["severity"] == "critical" and ev[0]["action"]
    ev, now = t.on_meas(meas(10, errors=["CHG_OVERTEMP"]))
    assert ev == []                                  # no duplicate
    ev, now = t.on_meas(meas(15))
    assert [e["type"] for e in ev] == ["fault_cleared"] and now


def test_health_levels():
    assert assess_health(meas(0), set(), 100)[0] == "green"
    assert assess_health(meas(0, temps=(41, 40)), set(), 100)[0] == "amber"
    assert assess_health(meas(0, temps=(46, 45)), set(), 100)[0] == "red"
    assert assess_health(meas(0), {"CELL_FAILURE"}, 100)[0] == "red"
    assert assess_health(meas(0), {"CHG_UNDERTEMP"}, 100)[0] == "amber"
    assert assess_health(meas(0), set(), 85)[0] == "amber"


def test_imbalance_ignored_near_full_but_judged_mid_range():
    v = [3.30] * 7 + [3.45]
    assert assess_health(meas(0, soc=97, volts=v), set(), 100)[0] == "green"
    assert assess_health(meas(0, soc=60, volts=v), set(), 100)[0] == "amber"


def test_status_events():
    t = DeviceTracker("d")
    assert t.on_status("online", "x") is None        # first online: nothing
    assert t.on_status("offline", "x")["type"] == "device_offline"
    assert t.on_status("online", "x")["type"] == "device_online"


def test_summary_shape():
    t = DeviceTracker("d")
    t.on_meas(meas(0))
    s = t.summary()
    assert s["health"] == "green" and s["health_score"] == 100
    assert {"soc_pct", "soh_pct", "cycles", "imbalance_v", "max_temp_c", "active_faults", "last_seen"} <= s.keys()
