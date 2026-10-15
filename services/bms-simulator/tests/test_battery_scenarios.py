"""The new scenarios on the simulator's battery (same physics as the firmware rig's; see the rig's tests)."""
from battery import Battery


def lowest_cell(b: Battery) -> float:
    return min(v for v in b.read_chip().cell_v if v > 0.5)


def test_forced_current_scenarios():
    for name, sign in (("charging", 1), ("discharging", -1)):
        b = Battery(seed=1, start_hour=2.0)
        b.set_scenario(name)
        b.step(60.0, True, True, 0)
        assert sign * b.current_a > 5.0, name


def test_overcurrent_exceeds_one_c():
    b = Battery(seed=1)
    b.set_scenario("overcurrent")
    b.step(60.0, True, True, 0)
    assert -b.current_a > b.nominal_ah


def test_low_soc_starts_nearly_empty():
    b = Battery(seed=1, start_hour=12.0)
    b.set_scenario("low_soc")
    for _ in range(60):
        b.step(60.0, True, True, 0)
    assert max(b.soc) < 0.15


def test_undervoltage_collapses_a_cell_even_on_a_small_pack():
    b = Battery(cells=2, seed=1)
    b.set_scenario("undervoltage")
    for _ in range(300):
        b.step(60.0, True, True, 0)
    assert lowest_cell(b) < 2.5


def test_instant_recovery_returns_to_normal_by_itself():
    b = Battery(seed=1)
    b.set_scenario("instant_recovery")
    for _ in range(11):
        b.step(60.0, True, True, 0)
    assert b.scenario == "normal" and b.force_c is None
