"""The BQ76952 protects the battery itself and reports it in SAFETY_STATUS A/B; native_sim's emulator does not."""
from chip import MAX_CELLS, ChipReading
from encoder import encode_safety
from protections import ChipProtections

COV, CUV, OCC, OCD1, SCD = 1 << 3, 1 << 2, 1 << 4, 1 << 5, 1 << 7          # SAFETY_STATUS_A bits
UTC, UTD, OTC, OTD, OTINT, OTF = 1 << 0, 1 << 1, 1 << 4, 1 << 5, 1 << 6, 1 << 7   # SAFETY_STATUS_B bits


def reading(cells=(3.3,) * 4, current=0.0, temp=25.0, ic=27.0, mosfet=29.0) -> ChipReading:
    return ChipReading(cell_v=list(cells) + [0.0] * (MAX_CELLS - len(cells)), current_a=current,
                       cell_temps_c=[temp, temp], ic_temp_c=ic, mosfet_temp_c=mosfet)


def status(p: ChipProtections, r: ChipReading) -> tuple[int, int]:
    a, b, _c = p.update(r)
    return a, b


def test_a_healthy_battery_raises_nothing():
    assert ChipProtections().update(reading()) == (0, 0, 0)


def test_cell_overvoltage_latches_until_it_falls_below_the_reset_level():
    p = ChipProtections()
    assert status(p, reading(cells=(3.3, 3.85, 3.3, 3.3)))[0] & COV
    assert status(p, reading(cells=(3.3, 3.6, 3.3, 3.3)))[0] & COV            # between reset and trip: stays
    assert not status(p, reading(cells=(3.3, 3.35, 3.3, 3.3)))[0] & COV


def test_cell_undervoltage_trips_below_2v5():
    assert status(ChipProtections(), reading(cells=(3.3, 2.4, 3.3, 3.3)))[0] & CUV


def test_unused_channels_do_not_look_like_undervoltage():
    assert not status(ChipProtections(), reading(cells=(3.3, 3.3)))[0] & CUV


def test_overtemperature_trips_charge_and_discharge_protection_and_clears_with_hysteresis():
    p = ChipProtections()
    a, b = status(p, reading(temp=46.0))
    assert b & OTC and b & OTD
    assert status(p, reading(temp=41.0))[1] & OTC                              # inside the 5 K hysteresis
    assert not status(p, reading(temp=39.0))[1] & OTC


def test_cold_battery_trips_undertemperature():
    p = ChipProtections()
    assert status(p, reading(temp=-5.0))[1] & UTC and not status(p, reading(temp=-5.0))[1] & UTD
    assert status(p, reading(temp=-25.0))[1] & UTD


def test_missing_temperature_sensor_is_ignored():
    assert status(ChipProtections(), reading(temp=-273.15))[1] & (UTC | UTD | OTC | OTD) == 0


def test_internal_and_mosfet_overtemperature():
    p = ChipProtections()
    assert status(p, reading(ic=90.0))[1] & OTINT
    assert status(p, reading(mosfet=110.0))[1] & OTF


def test_current_limits_follow_the_capacity():
    p = ChipProtections(capacity_ah=50.0)
    assert status(p, reading(current=51.0))[0] & OCC
    assert status(p, reading(current=0.0))[0] & OCC == 0
    assert status(p, reading(current=-51.0))[0] & OCD1
    assert status(p, reading(current=-101.0))[0] & SCD


def test_encode_safety_writes_the_three_status_registers():
    assert encode_safety((0x0C, 0x30, 0x00)) == [(0x03, b"\x0c"), (0x05, b"\x30"), (0x07, b"\x00")]
