"""ChipReading -> BQ76952 direct-command register writes, and the firmware shell commands that send them.

Register map and units come from the driver (`drivers/bms_ic/bq769x2/bq769x2.c`): cell voltages in mV,
stack voltage in 10 mV, current in 10 mA (positive = charging), temperatures in 0.1 K, all int16 little endian.
Pure functions: no I/O.
"""
from __future__ import annotations

from chip import ChipReading

BUS = "i2c@100"                  # the emulated I2C controller of native_sim
CHIP_ADDR = 0x08
# Zephyr's shell accepts 20 words (CONFIG_SHELL_ARGC_MAX): `i2c write <bus> <addr> <reg>` use 5 of them.
SHELL_MAX_BYTES = 15

REG_CELL_1 = 0x14
REG_STACK_V = 0x34
REG_CURRENT = 0x3A
REG_TEMP_INT = 0x68
REG_TEMP_TS1 = 0x70              # native_sim.overlay: cell-temp-pins = TS1
REG_TEMP_DCHG = 0x78             # native_sim.overlay: fet-temp-pin = DCHG

INT16_MAX, INT16_MIN = 32767, -32768


def _i16(value: float) -> bytes:
    """Round and clamp to a signed 16-bit little endian value (a reading must never wrap around)."""
    return max(INT16_MIN, min(INT16_MAX, round(value))).to_bytes(2, "little", signed=True)


def _kelvin_tenths(celsius: float) -> float:
    return (celsius + 273.15) * 10.0


def encode(reading: ChipReading) -> list[tuple[int, bytes]]:
    """(register, payload) pairs; contiguous registers are merged into one write."""
    cells = b"".join(_i16(v * 1000.0) for v in reading.cell_v)
    stack_10mv = sum(reading.cell_v) * 100.0
    return [
        (REG_CELL_1, cells),
        (REG_STACK_V, _i16(stack_10mv)),
        (REG_CURRENT, _i16(reading.current_a * 100.0)),
        (REG_TEMP_INT, _i16(_kelvin_tenths(reading.ic_temp_c))),
        (REG_TEMP_TS1, _i16(_kelvin_tenths(reading.cell_temps_c[0]))),
        (REG_TEMP_DCHG, _i16(_kelvin_tenths(reading.mosfet_temp_c))),
    ]


def i2c_write_cmd(reg: int, payload: bytes) -> str:
    """One firmware shell command, e.g. `i2c write i2c@100 0x8 0x14 0xe4 0x0c`."""
    return f"i2c write {BUS} {CHIP_ADDR:#x} {reg:#04x} " + " ".join(f"{b:#04x}" for b in payload)


def i2c_write_cmds(reg: int, payload: bytes) -> list[str]:
    """Like i2c_write_cmd, split so no command carries more than SHELL_MAX_BYTES data bytes."""
    return [i2c_write_cmd(reg + start, payload[start:start + SHELL_MAX_BYTES])
            for start in range(0, len(payload), SHELL_MAX_BYTES)]


REG_SAFETY_STATUS_A, REG_SAFETY_STATUS_B, REG_SAFETY_STATUS_C = 0x03, 0x05, 0x07


def encode_safety(status: tuple[int, int, int]) -> list[tuple[int, bytes]]:
    """SAFETY_STATUS A/B/C, as produced by ChipProtections (the emulator does not model the chip's trips)."""
    return [(reg, bytes([value & 0xFF]))
            for reg, value in zip((REG_SAFETY_STATUS_A, REG_SAFETY_STATUS_B, REG_SAFETY_STATUS_C), status)]
