import pytest

from physical_battery import SCENARIOS, ocv
from schema import Meas
from sim import Simulator
from thingset import decode_live

HOUR = 3600
DAY = 24 * HOUR


def run(sim: Simulator, seconds: float, dt: float = 600.0):
    t = 0.0
    while t < seconds:
        sim.step(dt)
        t += dt
        yield sim.snapshot()


def as_meas(snap: dict) -> Meas:
    return Meas.model_validate({"schema": 1, "device_id": "t", "ts": "2026-01-01T00:00:00Z", **snap})


def test_ocv_is_flat_in_the_middle_and_collapses_below_zero():
    assert ocv(0.5) == pytest.approx(3.265, abs=0.005)
    assert ocv(1.0) == pytest.approx(3.392)
    assert ocv(-0.05) < 2.5


def test_same_seed_gives_identical_runs():
    a = [s["soc_pct"] for s in run(Simulator(seed=7), DAY)]
    b = [s["soc_pct"] for s in run(Simulator(seed=7), DAY)]
    assert a == b


def test_normal_week_raises_no_fault_and_stays_normal():
    sim = Simulator(seed=1)
    for snap in run(sim, 7 * DAY):
        assert snap["errors"] == []
        assert snap["state"] == "NORMAL"
        assert 0.0 <= snap["soc_pct"] <= 100.0
        assert 20.0 < max(snap["cell_temps_c"]) < 40.0


def test_every_snapshot_matches_the_schema_in_every_scenario():
    for scenario in SCENARIOS:
        for snap in run(Simulator(seed=2, scenario=scenario), DAY):
            as_meas(snap)


def test_the_live_line_always_has_a_valid_crc_and_agrees_with_the_snapshot():
    sim = Simulator(seed=3)
    sim.step(5000)
    meas = decode_live(sim.live_line())            # raises on a bad CRC
    assert meas["rSOC_pct"] == pytest.approx(sim.snapshot()["soc_pct"], abs=0.05)
    assert len(meas["rCellVoltages_V"]) == 16


def test_charging_raises_soc_and_discharging_lowers_it():
    sim = Simulator(seed=1, start_hour=9.0)
    before = sim.snapshot()["soc_pct"]
    sim.step(HOUR)
    assert sim.snapshot()["soc_pct"] > before
    sim = Simulator(seed=1, start_hour=19.0)
    before = sim.snapshot()["soc_pct"]
    sim.step(HOUR)
    assert sim.snapshot()["soc_pct"] < before


def test_firmware_soc_is_an_estimate_not_the_truth():
    sim = Simulator(seed=1)
    list(run(sim, 2 * DAY))
    err = sim.debug()["soc_estimate_error_pct"]
    assert err != 0.0 and abs(err) < 10.0


def test_balancing_eventually_evens_out_the_cells():
    sim = Simulator(seed=1, start_hour=9.0)
    spread0 = None
    for snap in run(sim, 12 * DAY):
        v = snap["cell_voltages_v"]
        if spread0 is None:
            spread0 = max(v) - min(v)
    v = sim.snapshot()["cell_voltages_v"]
    assert max(v) - min(v) < 0.010


def test_cell_drift_ends_in_a_cell_failure_within_a_day():
    sim = Simulator(seed=1, scenario="cell_drift")
    seen = set()
    for snap in run(sim, DAY, dt=60.0):
        seen.update(snap["errors"])
    assert "CELL_FAILURE" in seen
    assert sim.snapshot()["state"] == "OFF"


def test_overheat_trips_the_temperature_flags_and_opens_the_switches():
    sim = Simulator(seed=1, scenario="overheat")
    seen = set()
    for snap in run(sim, 4 * HOUR, dt=60.0):
        seen.update(snap["errors"])
    assert {"CHG_OVERTEMP", "DIS_OVERTEMP"} <= seen
    assert sim.snapshot()["state"] == "OFF"


def test_switching_back_to_normal_restores_the_cells_but_a_cooled_pack_recovers():
    sim = Simulator(seed=1, scenario="overheat")
    list(run(sim, 3 * HOUR, dt=60.0))
    sim.set_scenario("normal")
    list(run(sim, 6 * HOUR, dt=60.0))
    assert sim.snapshot()["errors"] == []


def test_unknown_scenario_is_rejected():
    with pytest.raises(ValueError):
        Simulator().set_scenario("bogus")
