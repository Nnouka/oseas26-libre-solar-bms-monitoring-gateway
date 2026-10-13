import pytest

from chemistry import OCV_V, SOC_PCT, interp


def test_tables_have_matching_lengths_and_increase():
    assert len(OCV_V) == len(SOC_PCT) == 21
    assert SOC_PCT == sorted(SOC_PCT)
    assert OCV_V == sorted(OCV_V)


def test_interp_hits_the_table_points():
    assert interp(0.0, SOC_PCT, OCV_V) == 2.833
    assert interp(45.0, SOC_PCT, OCV_V) == pytest.approx(3.264)
    assert interp(100.0, SOC_PCT, OCV_V) == 3.392


def test_interp_is_halfway_between_two_points():
    assert interp(92.5, SOC_PCT, OCV_V) == pytest.approx((3.309 + 3.314) / 2)


def test_interp_clamps_outside_the_table():
    assert interp(-10.0, SOC_PCT, OCV_V) == 2.833
    assert interp(250.0, SOC_PCT, OCV_V) == 3.392


def test_interp_works_the_other_way_round_too():
    # voltage -> charge: this is how the firmware estimates SoC at boot
    assert interp(3.264, OCV_V, SOC_PCT) == pytest.approx(45.0)
    assert interp(0.0, OCV_V, SOC_PCT) == 0.0
    assert interp(9.0, OCV_V, SOC_PCT) == 100.0
