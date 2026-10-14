from __future__ import annotations

import json
import math
import zlib

GROUP = "mLive"

# (item name, how the firmware prints it); the ORDER is the order on the wire
ITEMS: list[tuple[str, str]] = [
    ("rPackVoltage_V", "f2"),      # f2 = float with 2 decimals
    ("rStackVoltage_V", "f2"),
    ("rPackCurrent_A", "f2"),
    ("rCellTemps_degC", "arr1"),   # arr1 = list of floats with 1 decimal
    ("rICTemp_degC", "f1"),
    ("rMOSFETTemp_degC", "f1"),
    ("rSOC_pct", "f1"),
    ("rErrorFlags", "int"),
    ("rBmsState", "int"),
    ("rCellVoltages_V", "arr3"),
    ("rCellAvgVoltage_V", "f3"),
    ("rCellMinVoltage_V", "f3"),
    ("rCellMaxVoltage_V", "f3"),
    ("rBalancingStatus", "int"),
]


def _float(value: float | None, decimals: int) -> str:
    """'null' for None or NaN, else the number with `decimals` decimals."""
    # The firmware prints NaN as JSON null.
    if value is None or math.isnan(value):
        return "null"
    return f"{value:.{decimals}f}"


def _format(value, kind: str) -> str:
    """Print one item according to its kind ('int', 'f2', 'arr3', ...)."""
    if kind == "int":
        return str(int(value))
    if kind.startswith("arr"):
        decimals = int(kind[3:])
        return "[" + ",".join(_float(v, decimals) for v in value) + "]"
    return _float(value, int(kind[1:]))


def crc32_hex(text: str) -> str:
    """CRC-32 of the text as 8 upper-case hex digits."""
    return f"{zlib.crc32(text.encode('ascii')) & 0xFFFFFFFF:08X}"


def encode_live(meas: dict) -> str:
    """Build the full '#mLive {...} CRC#' line from a dict of Meas items."""
    body = ",".join(f'"{name}":{_format(meas[name], kind)}' for name, kind in ITEMS)
    statement = f'#{GROUP} {{"Meas":{{{body}}}}}'
    return f"{statement} {crc32_hex(statement)}#"


def decode_live(line: str) -> dict:
    """Check the CRC and return the Meas items. Raise ValueError for a bad line."""
    line = line.strip()
    if not (line.startswith(f"#{GROUP} ") and line.endswith("#")):
        raise ValueError("not a #mLive statement")
    statement, _, crc = line[:-1].rpartition(" ")
    if crc32_hex(statement) != crc:
        raise ValueError(f"CRC mismatch: line says {crc}, data gives {crc32_hex(statement)}")
    payload = statement[len(f"#{GROUP} "):]
    return json.loads(payload)["Meas"]
