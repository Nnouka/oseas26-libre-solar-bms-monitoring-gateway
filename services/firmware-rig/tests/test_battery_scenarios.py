"""The new scenarios, on the physical battery alone (no firmware)."""
from battery import SCENARIOS, Battery
from chip import ChipReading
from protections import ChipProtections

OCD1 = 1 << 5          # SAFETY_STATUS_A bit, see test_protections.py


def step(b: Battery, n: int = 1, dt: float = 60.0) -> None:
    for _ in range(n):
        b.step(dt, True, True, 0)


def test_all_scenario_names_are_registered():
    assert SCENARIOS == ("normal", "cell_drift", "overheat", "cloudy", "charging", "discharging", "low_soc",
                         "overcurrent", "undervoltage", "instant_recovery")


def test_charging_forces_a_charging_current_at_any_hour():
    for hour in (2.0, 12.0, 22.0):
        b = Battery(seed=1, start_hour=hour)
        b.set_scenario("charging")
        step(b)
        assert 5.0 < b.current_a < 15.0, hour


def test_discharging_forces_a_discharge_current_at_any_hour():
    for hour in (2.0, 12.0, 22.0):
        b = Battery(seed=1, start_hour=hour)
        b.set_scenario("discharging")
        step(b)
        assert -15.0 < b.current_a < -5.0, hour


def test_low_soc_starts_the_pack_nearly_empty_and_keeps_it_there():
    b = Battery(seed=1, start_hour=12.0)                     # midday sun would normally charge it
    b.set_scenario("low_soc")
    assert all(0.10 <= s <= 0.14 for s in b.soc)
    step(b, 60)
    assert max(b.soc) < 0.15


def test_low_soc_resets_the_charge_every_time_it_is_selected():
    b = Battery(seed=1)
    b.set_scenario("charging")
    step(b, 120)
    b.set_scenario("low_soc")
    assert max(b.soc) <= 0.14


def test_overcurrent_exceeds_one_c_and_trips_the_discharge_protection():
    b = Battery(seed=1)
    b.set_scenario("overcurrent")
    step(b)
    assert -b.current_a > b.nominal_ah                       # more than 1 C
    status_a, _b, _c = ChipProtections(capacity_ah=b.nominal_ah).update(b.read_chip())
    assert status_a & OCD1


def test_overcurrent_stops_when_the_bms_opens_the_discharge_fet():
    b = Battery(seed=1)
    b.set_scenario("overcurrent")
    b.step(60.0, True, False, 0)                             # dis_on False
    assert b.current_a == 0.0


def test_leaving_a_forced_scenario_restores_the_normal_profile():
    b = Battery(seed=1, start_hour=2.0)                      # night: normal current is a small discharge
    b.set_scenario("charging")
    step(b)
    assert b.current_a > 5.0
    b.set_scenario("normal")
    assert b.force_c is None
    step(b)
    assert b.current_a < 1.0


def test_existing_scenarios_have_no_forced_current():
    for name in ("normal", "cell_drift", "overheat", "cloudy"):
        b = Battery(seed=1)
        b.set_scenario(name)
        assert b.force_c is None, name
