import pytest

from chip import MAX_CELLS, ChipReading
from firmware import ERR, NORMAL, OFF, Firmware


def reading(cell_v=3.3, n=8, current=0.0, temp=25.0):
    return ChipReading([cell_v] * n + [0.0] * (MAX_CELLS - n), current, [temp, temp], temp, temp)


def booted(**kw) -> Firmware:
    fw = Firmware()
    fw.boot(reading(**kw))
    return fw


def run(fw, r, seconds, start_ms=0.0, step_s=10):
    t = start_ms
    for _ in range(int(seconds / step_s)):
        t += step_s * 1000
        fw.update(r, t)
    return t


def test_boot_estimates_soc_from_the_average_cell_voltage():
    assert booted(cell_v=3.264).soc == pytest.approx(45.0)     # 3.264 V is the 45 % point


def test_state_machine_goes_off_dis_normal():
    fw = booted()
    assert fw.state == OFF
    fw.update(reading(), 1000)
    assert fw.state != OFF
    fw.update(reading(), 2000)
    assert fw.state == NORMAL


def test_coulomb_counting_moves_soc_by_whole_steps():
    fw = booted(cell_v=3.264)                    # 45 %
    start = fw.soc
    t = run(fw, reading(current=25.0), 3600)     # 25 A for an hour into 50 Ah = +50 %
    assert fw.soc == pytest.approx(min(100.0, start + 50.0), abs=0.2)
    t = run(fw, reading(current=-10.0), 1800, start_ms=t)    # 5 Ah out = -10 %
    assert fw.soc < 100.0


def test_soc_is_clamped():
    fw = booted(cell_v=3.390)
    run(fw, reading(current=50.0), 7200)
    assert fw.soc == 100.0


def test_overvoltage_latches_until_below_the_reset_voltage():
    fw = booted()
    fw.update(reading(cell_v=3.81), 1000)
    assert fw.flags & ERR["CELL_OVERVOLTAGE"]
    fw.update(reading(cell_v=3.60), 2000)     # below the limit, but above the 3.40 reset
    assert fw.flags & ERR["CELL_OVERVOLTAGE"]
    fw.update(reading(cell_v=3.39), 3000)
    assert not fw.flags & ERR["CELL_OVERVOLTAGE"]


def test_undervoltage_blocks_discharge_but_not_charge():
    fw = booted()
    fw.update(reading(cell_v=2.49), 1000)
    assert fw.flags & ERR["CELL_UNDERVOLTAGE"]
    assert not fw.dis_allowed() and fw.chg_allowed()
    fw.update(reading(cell_v=2.49), 2000)
    assert fw.chg_on and not fw.dis_on


def test_over_temperature_blocks_both_and_clears_with_hysteresis():
    fw = booted()
    fw.update(reading(temp=46.0), 1000)
    assert fw.flags & ERR["CHG_OVERTEMP"] and fw.flags & ERR["DIS_OVERTEMP"]
    fw.update(reading(temp=42.0), 2000)         # inside the limit but not 5 degC inside
    assert fw.flags & ERR["CHG_OVERTEMP"]
    fw.update(reading(temp=39.9), 3000)
    assert fw.flags == 0


def test_cold_battery_blocks_charging_only():
    fw = booted()
    fw.update(reading(temp=-5.0), 1000)
    assert fw.flags & ERR["CHG_UNDERTEMP"]
    assert fw.dis_allowed() and not fw.chg_allowed()


def test_balancing_waits_for_30_idle_minutes():
    cells = [3.35, 3.40, 3.35, 3.35, 3.35, 3.35, 3.35, 3.35] + [0.0] * 8
    r = ChipReading(cells, 0.0, [25.0, 25.0], 25.0, 25.0)
    fw = Firmware()
    fw.boot(r)
    t = run(fw, r, 1700)
    assert fw.balancing_mask == 0                # not yet: 1700 s < 1800 s
    run(fw, r, 200, start_ms=t)
    assert fw.balancing_mask == 0b10             # only cell 1 (index 1) is high enough above the rest


def test_load_resets_the_balancing_timer():
    cells = [3.35, 3.40] + [3.35] * 6 + [0.0] * 8
    idle = ChipReading(cells, 0.0, [25.0, 25.0], 25.0, 25.0)
    busy = ChipReading(cells, 5.0, [25.0, 25.0], 25.0, 25.0)
    fw = Firmware()
    fw.boot(idle)
    t = run(fw, idle, 1700)
    t = run(fw, busy, 10, start_ms=t)
    t = run(fw, idle, 1700, start_ms=t)
    assert fw.balancing_mask == 0


def test_meas_has_the_quirks_of_the_real_firmware():
    meas = Firmware().meas()
    assert meas["rCellMinVoltage_V"] == 10.0 and meas["rCellMaxVoltage_V"] == 0.0
    assert len(meas["rCellVoltages_V"]) == MAX_CELLS
