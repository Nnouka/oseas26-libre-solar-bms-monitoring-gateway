import pytest

from chip import MAX_CELLS, ChipReading
from encoder import SHELL_MAX_BYTES, encode, i2c_write_cmd, i2c_write_cmds


def reading(**kw) -> ChipReading:
    base = dict(cell_v=[3.300] + [0.0] * (MAX_CELLS - 1), current_a=0.0, cell_temps_c=[25.0, 24.0],
                ic_temp_c=27.0, mosfet_temp_c=29.0)
    return ChipReading(**{**base, **kw})


def writes(r: ChipReading) -> dict[int, bytes]:
    """Flatten the encoder output into register -> byte, so tests can look at single registers."""
    flat: dict[int, bytes] = {}
    for reg, payload in encode(r):
        for i, b in enumerate(payload):
            flat[reg + i] = bytes([b])
    return flat


def word(r: ChipReading, reg: int) -> int:
    w = writes(r)
    return int.from_bytes(w[reg] + w[reg + 1], "little", signed=True)


def test_cell_voltages_are_millivolts_little_endian():
    assert writes(reading())[0x14] + writes(reading())[0x15] == bytes.fromhex("e40c")   # 3300 mV


def test_unused_channels_are_zero_millivolts():
    r = reading(cell_v=[3.3, 3.31] + [0.0] * 14)
    assert [word(r, 0x14 + 2 * i) for i in range(4)] == [3300, 3310, 0, 0]


def test_charging_current_is_positive_in_10ma_units():
    assert word(reading(current_a=1.0), 0x3A) == 100


def test_discharge_current_is_signed():
    assert word(reading(current_a=-2.5), 0x3A) == -250


def test_temperatures_are_tenths_of_kelvin_in_the_thermistor_mosfet_and_internal_registers():
    r = reading(cell_temps_c=[25.0, 24.0], mosfet_temp_c=29.0, ic_temp_c=27.0)
    assert word(r, 0x70) == round((25.0 + 273.15) * 10)
    assert word(r, 0x78) == round((29.0 + 273.15) * 10)
    assert word(r, 0x68) == round((27.0 + 273.15) * 10)


def test_cold_site_temperature_stays_a_valid_signed_value():
    assert word(reading(cell_temps_c=[-20.0, -20.0]), 0x70) == round((-20.0 + 273.15) * 10)


def test_stack_voltage_is_the_cell_sum_in_10mv_units():
    r = reading(cell_v=[3.3, 3.3, 3.3, 3.3] + [0.0] * 12)
    assert word(r, 0x34) == 1320


def test_values_clamp_instead_of_wrapping():
    r = reading(cell_v=[40.0] + [0.0] * 15, current_a=400.0)
    assert word(r, 0x14) == 32767
    assert word(r, 0x3A) == 32767
    assert word(reading(current_a=-400.0), 0x3A) == -32768


def test_contiguous_registers_are_merged_into_one_write():
    cell_write = [p for reg, p in encode(reading()) if reg == 0x14]
    assert len(cell_write) == 1 and len(cell_write[0]) == 2 * MAX_CELLS


def test_i2c_write_cmd_format():
    assert i2c_write_cmd(0x14, bytes.fromhex("e40c")) == "i2c write i2c@100 0x8 0x14 0xe4 0x0c"


def test_long_payloads_are_split_to_the_shell_buffer_size_and_registers_advance():
    cmds = i2c_write_cmds(0x14, bytes(range(32)))
    assert [len(c.split()[5:]) for c in cmds] == [SHELL_MAX_BYTES, SHELL_MAX_BYTES, 32 - 2 * SHELL_MAX_BYTES]
    assert [c.split()[4] for c in cmds] == ["0x14", hex(0x14 + SHELL_MAX_BYTES), hex(0x14 + 2 * SHELL_MAX_BYTES)]


def test_a_command_stays_under_the_shells_argument_limit():
    """Zephyr's shell refuses more than CONFIG_SHELL_ARGC_MAX (20) words: 'Too many arguments in the command.'"""
    for reg, payload in encode(reading()):
        for cmd in i2c_write_cmds(reg, payload):
            assert len(cmd.split()) <= 20
