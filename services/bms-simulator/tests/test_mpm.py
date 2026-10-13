"""The MPM filter: pure logic, no broker, no clock (we pass `now` ourselves)."""
from mpm import MpmEvent, MpmFilter


def meas(**kw) -> dict:
    base = {"schema": 1, "device_id": "dev1", "ts": "2026-10-13T08:30:00Z", "state": "NORMAL",
            "pack_voltage_v": 26.1, "pack_current_a": 3.0, "soc_pct": 60.0,
            "cell_voltages_v": [3.27, 3.27, 3.28, 3.27], "cell_temps_c": [25.0, 26.0],
            "mosfet_temp_c": 27.0, "balancing_mask": 0, "errors": []}
    return {**base, **kw}


def test_first_measurement_is_sent_then_a_steady_stream_is_silent():
    f = MpmFilter()
    assert f.check(meas(), 0)["reason"] == "first"
    assert [f.check(meas(), t) for t in range(1, 60)] == [None] * 59


def test_a_fault_appearing_or_clearing_is_sent():
    f = MpmFilter()
    f.check(meas(), 0)
    assert f.check(meas(errors=["CHG_OVERTEMP"]), 1)["reason"] == "errors_changed"
    assert f.check(meas(errors=["CHG_OVERTEMP"]), 2) is None          # same fault: nothing new
    assert f.check(meas(errors=[]), 3)["reason"] == "errors_changed"


def test_state_change_and_soc_step():
    f = MpmFilter(soc_step_pct=5.0)
    f.check(meas(), 0)
    assert f.check(meas(soc_pct=63.0), 1) is None                     # small drift is not a decision
    assert f.check(meas(soc_pct=65.5), 2)["reason"] == "soc_step"     # 5.5 away from the 60 MPM heard
    assert f.check(meas(soc_pct=65.5, state="CHG"), 3)["reason"] == "state_change"


def test_imbalance_is_sent_when_it_crosses_the_limit_in_either_direction():
    f = MpmFilter(imbalance_mv=50.0)
    f.check(meas(), 0)
    bad = meas(cell_voltages_v=[3.20, 3.27, 3.28, 3.27])              # 80 mV spread
    assert f.check(bad, 1)["reason"] == "imbalance_high"
    assert f.check(bad, 2) is None
    assert f.check(meas(), 3)["reason"] == "imbalance_ok"


def test_heartbeat_when_nothing_else_happens():
    f = MpmFilter(heartbeat_s=300.0)
    f.check(meas(), 0)
    assert f.check(meas(), 299) is None
    assert f.check(meas(), 300)["reason"] == "heartbeat"
    assert f.check(meas(), 301) is None                               # the timer restarted


def test_the_event_is_compact_and_valid():
    event = MpmFilter().check(meas(errors=["CHG_OVERTEMP"]), 0)
    MpmEvent.model_validate(event)
    assert event["imbalance_mv"] == 10.0 and event["max_temp_c"] == 27.0
    assert "cell_voltages_v" not in event and "cell_temps_c" not in event
