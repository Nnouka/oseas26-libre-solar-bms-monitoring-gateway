"""The physical battery on its own"""
import pytest

from physical_battery import SCENARIOS, TAPER_BAND, Battery, ocv
from chip import MAX_CELLS


def cells_of(reading):
    return [v for v in reading.cell_v if v > 0.5]


def test_ocv_is_flat_in_the_middle_and_collapses_below_zero():
    assert ocv(0.5) == pytest.approx(3.265, abs=0.005)
    assert ocv(1.0) == pytest.approx(3.392)
    assert ocv(-0.05) < 2.5


def test_the_cells_are_not_identical_and_the_chip_pads_to_16_channels():
    b = Battery(cells=8, seed=1)
    reading = b.read_chip()
    assert len(reading.cell_v) == MAX_CELLS
    assert reading.cell_v[8:] == [0.0] * 8
    assert len(set(cells_of(reading))) > 1


def test_same_seed_same_battery():
    a, b = Battery(seed=5), Battery(seed=5)
    assert a.soc == b.soc and a.cap == b.cap


def test_charging_raises_the_true_charge():
    b = Battery(seed=1, start_hour=9.0)
    before = sum(b.soc)
    b.step(3600, chg_on=True, dis_on=True, bleed_mask=0)
    assert sum(b.soc) > before and b.current_a > 0


def test_discharging_lowers_the_true_charge():
    b = Battery(seed=1, start_hour=19.0)
    before = sum(b.soc)
    b.step(3600, chg_on=True, dis_on=True, bleed_mask=0)
    assert sum(b.soc) < before and b.current_a < 0


def test_an_open_switch_stops_the_current():
    b = Battery(seed=1, start_hour=9.0)
    b.step(60, chg_on=False, dis_on=True, bleed_mask=0)
    assert b.current_a == 0.0
    b = Battery(seed=1, start_hour=19.0)
    b.step(60, chg_on=True, dis_on=False, bleed_mask=0)
    assert b.current_a == 0.0


def test_the_charger_tapers_to_zero_when_the_pack_is_full():
    b = Battery(seed=1, start_hour=9.0)
    for _ in range(36):                                # three hours of morning sun
        b.step(300, chg_on=True, dis_on=True, bleed_mask=0)
    assert max(b.soc) <= 1.0 + 1e-9
    assert abs(b.current_a) < 0.5


def test_current_makes_the_terminal_voltage_sag_or_rise():
    b = Battery(seed=1)
    rest = b.terminal_voltages(0.0)
    assert all(v > r for v, r in zip(b.terminal_voltages(20.0), rest))
    assert all(v < r for v, r in zip(b.terminal_voltages(-20.0), rest))


def test_current_heats_the_pack_and_it_cools_back():
    b = Battery(seed=1, start_hour=0.0)
    b.temp_c = 28.0
    b.current_a = 0.0
    b.temp_c += 5.0                                    # heat it by hand
    for _ in range(30):
        b.step(300, chg_on=False, dis_on=False, bleed_mask=0)   # no current, only cooling
    assert b.temp_c < 29.0


def test_bleeding_lowers_one_cell_only():
    a, b = Battery(seed=1, start_hour=3.0), Battery(seed=1, start_hour=3.0)
    a.step(3600, False, False, bleed_mask=0b100)
    b.step(3600, False, False, bleed_mask=0)
    assert a.soc[2] < b.soc[2]
    assert a.soc[1] == pytest.approx(b.soc[1])


def test_scenarios_are_known_and_unknown_ones_rejected():
    assert SCENARIOS == ("normal", "cell_drift", "overheat", "cloudy")
    with pytest.raises(ValueError):
        Battery().set_scenario("bogus")


def test_overheat_raises_the_ambient_temperature():
    b = Battery(seed=1)
    b.set_scenario("overheat")
    for _ in range(60):
        b.step(60, True, True, 0)
    assert b.ambient_c > 40.0


def test_cell_drift_pulls_one_cell_down():
    b = Battery(seed=1)
    b.set_scenario("cell_drift")
    for _ in range(300):
        b.step(60, False, False, 0)
    assert b.soc[2] < min(b.soc[k] for k in range(8) if k != 2) - 0.1


def test_cloudy_midday_current_is_much_lower_than_clear_sky():
    clear, cloudy = Battery(seed=1, start_hour=12.0), Battery(seed=1, start_hour=12.0)
    cloudy.set_scenario("cloudy")
    clear.step(60, True, True, 0)
    cloudy.step(60, True, True, 0)
    assert 0.0 < cloudy.current_a < 0.4 * clear.current_a


def test_a_cloudy_day_does_not_fill_the_pack():
    clear, cloudy = Battery(seed=1, start_hour=6.0), Battery(seed=1, start_hour=6.0)
    cloudy.set_scenario("cloudy")
    clear_peak = cloudy_peak = 0.0
    for _ in range(72):                                # 06:00 to 18:00 in 10-minute steps
        clear.step(600, True, True, 0)
        cloudy.step(600, True, True, 0)
        clear_peak = max(clear_peak, max(clear.soc))
        cloudy_peak = max(cloudy_peak, max(cloudy.soc))
    assert clear_peak > 0.99                           # the sunny pack fills up...
    assert cloudy_peak < 1.0 - TAPER_BAND              # ...the cloudy one never even reaches the taper zone
    assert sum(cloudy.soc) < sum(clear.soc)


def test_leaving_the_cloudy_scenario_brings_the_sun_back():
    b = Battery(seed=1, start_hour=12.0)
    b.set_scenario("cloudy")
    assert b.cloud < 1.0
    b.set_scenario("normal")
    assert b.cloud == 1.0
