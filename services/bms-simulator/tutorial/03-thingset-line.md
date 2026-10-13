# Part 3: The ThingSet line

**Goal.** Write `thingset.py`: turn a dictionary of measurements into the exact `#mLive ... CRC#` line the firmware prints, and
turn such a line back into a dictionary after checking its checksum. Test it against the real line you captured in Part 0.

**Time.** 1.5 hours.

## Ideas

- **A wire format is a contract.** Whoever reads the line (the Cicada relay, a ThingSet tool, our own ingestor) does not care how
  you computed the numbers. It cares that the text is *exactly* right: the same item names, the same order, the same number of decimals,
  `null` for "not a number". So we aim for byte-for-byte equality with the real thing.
- **Number formatting.** In Python, `f"{value:.3f}"` prints three decimals. `f"{0.5:.2f}"` is `0.50`. The firmware uses C's `printf`, which behaves
  the same for the numbers we use.
- **JSON `null`.** Python's `None` (or `math.nan`) must become the text `null`.
- **CRC-32** is a checksum: 32 bits computed from the bytes of a message. If one character is flipped in transit, the checksum almost
  certainly changes. Python has it in the standard library: `zlib.crc32(data_bytes)`. The firmware prints it as 8 upper-case hex digits.
- **What is checksummed.** From the `#` up to and including the closing `}` of the JSON, and *not* the space before the CRC. You verified this by hand in Part 0.

The line, taken apart:

```text
#mLive {"Meas":{"rPackVoltage_V":0.00, ... ,"rBalancingStatus":0}} 437123CA#
^^^^^^ ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ ^^^^^^^^
marker JSON payload (no spaces at all)                         CRC-32 of "#mLive {...}", then a closing #
```

## Steps

### 3.1 The tests (type them in)

`tests/test_thingset.py`. Paste the line you captured in Part 0 into `NATIVE_SIM_LINE` if yours differs from this one (the empty-chip line is stable, so it should be the same):

```python
import pytest

from thingset import crc32_hex, decode_live, encode_live

# Captured from the real firmware running in native_sim (docs/native_sim-tutorial.md).
NATIVE_SIM_LINE = (
    '#mLive {"Meas":{"rPackVoltage_V":0.00,"rStackVoltage_V":0.00,"rPackCurrent_A":0.00,'
    '"rCellTemps_degC":[-273.1,0.0],"rICTemp_degC":-273.1,"rMOSFETTemp_degC":-273.1,'
    '"rSOC_pct":0.0,"rErrorFlags":0,"rBmsState":3,"rCellVoltages_V":[0.000,0.000,0.000,0.000,'
    '0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000,0.000],'
    '"rCellAvgVoltage_V":null,"rCellMinVoltage_V":10.000,"rCellMaxVoltage_V":0.000,'
    '"rBalancingStatus":0}} 437123CA#'
)


def test_crc_of_the_captured_line_is_correct():
    statement = NATIVE_SIM_LINE[:-1].rpartition(" ")[0]
    assert crc32_hex(statement) == "437123CA"


def test_decode_reads_the_captured_line():
    meas = decode_live(NATIVE_SIM_LINE)
    assert meas["rBmsState"] == 3
    assert meas["rCellAvgVoltage_V"] is None          # JSON null
    assert len(meas["rCellVoltages_V"]) == 16
    assert meas["rCellMinVoltage_V"] == 10.0


def test_encode_of_decode_gives_back_the_identical_line():
    assert encode_live(decode_live(NATIVE_SIM_LINE)) == NATIVE_SIM_LINE


def test_decode_rejects_a_corrupted_line():
    bad = NATIVE_SIM_LINE.replace('"rBmsState":3', '"rBmsState":2')
    with pytest.raises(ValueError, match="CRC"):
        decode_live(bad)
    with pytest.raises(ValueError):
        decode_live("hello")
```

Run `uv run pytest tests/test_thingset.py -q` and watch it fail with `ModuleNotFoundError: No module named 'thingset'`. Good.

### 3.2 The skeleton

Create `thingset.py`. The table says which items exist, in which order, and how each is printed. Fill in the four functions.

```python
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
    ...


def _format(value, kind: str) -> str:
    """Print one item according to its kind ('int', 'f2', 'arr3', ...)."""
    ...


def crc32_hex(text: str) -> str:
    """CRC-32 of the text as 8 upper-case hex digits."""
    ...


def encode_live(meas: dict) -> str:
    """Build the full '#mLive {...} CRC#' line from a dict of Meas items."""
    ...


def decode_live(line: str) -> dict:
    """Check the CRC and return the Meas items. Raise ValueError for a bad line."""
    ...
```

### 3.3 Hints, in the order you will need them

1. **`_float`**: `math.isnan(x)` is true for `float("nan")`. Check `None` first.
2. **`_format`**: `kind` is a string like `"arr3"`. `kind.startswith("arr")` tells arrays apart, `int(kind[3:])` gives the number of decimals.
   For arrays, join with `","` (a comma and **no space**) and wrap in `[` `]`.
3. **`crc32_hex`**: `zlib.crc32(text.encode("ascii"))` returns an int, which may be negative on old Pythons. Use `& 0xFFFFFFFF`. Format with
   `f"{n:08X}"` (eight digits, zero padded, upper case).
4. **`encode_live`**: build the JSON text *by hand* from `ITEMS` instead of using `json.dumps`. `json.dumps` cannot print `0.50` as `0.50` (it
   drops trailing zeros) and the real line has no spaces. Each item is `f'"{name}":{printed value}'`, joined with commas, inside `{"Meas":{...}}`.
   Put `#mLive ` in front (the statement), compute the CRC of that statement, then append `f" {crc}#"`.
5. **`decode_live`**: strip whitespace; the line must start with `#mLive ` and end with `#`. Remove the last `#`, then
   `rpartition(" ")` splits into *statement* and *crc* at the *last* space. Recompute the CRC of the statement, compare, raise `ValueError` with a clear
   message if different. The JSON starts after `"#mLive "`; `json.loads` understands `null`.

### 3.4 Run the tests

```bash
uv run pytest tests/test_thingset.py -q
```

Expected: `4 passed`. The most telling test is `test_encode_of_decode_gives_back_the_identical_line`. If it fails, pytest
prints both lines. Compare them character by character. The typical culprits are a space after a comma, `0.0` instead of `0.00`, and `nan` instead of `null`.

<details>
<summary>Solution: thingset.py (open after you tried)</summary>

```python
"""The ThingSet `#mLive` statement: encode, decode, CRC.  No physics, no MQTT.

A line looks like this (one line, shortened here):

    #mLive {"Meas":{"rPackVoltage_V":26.31, ...}} 437123CA#
    ^^^^^^ ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^ ^^^^^^^^
    |      JSON payload                           CRC-32 of "#mLive {...}"
    statement marker + name of the reported subset
"""
from __future__ import annotations

import json
import math
import zlib

GROUP = "mLive"

# (item name, how the firmware prints it).  The ORDER is the order on the wire.
#   "f2" = float with 2 decimals, "arr3" = array of floats with 3 decimals, ...
ITEMS: list[tuple[str, str]] = [
    ("rPackVoltage_V", "f2"),
    ("rStackVoltage_V", "f2"),
    ("rPackCurrent_A", "f2"),
    ("rCellTemps_degC", "arr1"),
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
    # The firmware prints NaN as JSON null.
    if value is None or math.isnan(value):
        return "null"
    return f"{value:.{decimals}f}"


def _format(value, kind: str) -> str:
    if kind == "int":
        return str(int(value))
    if kind.startswith("arr"):
        decimals = int(kind[3:])
        return "[" + ",".join(_float(v, decimals) for v in value) + "]"
    return _float(value, int(kind[1:]))


def crc32_hex(text: str) -> str:
    """8 upper-case hex digits, as the firmware prints them."""
    return f"{zlib.crc32(text.encode('ascii')) & 0xFFFFFFFF:08X}"


def encode_live(meas: dict) -> str:
    """Build the full `#mLive ... CRC#` line from a dict of `Meas` items."""
    body = ",".join(f'"{name}":{_format(meas[name], kind)}' for name, kind in ITEMS)
    statement = f'#{GROUP} {{"Meas":{{{body}}}}}'
    return f"{statement} {crc32_hex(statement)}#"


def decode_live(line: str) -> dict:
    """Check the CRC and return the `Meas` items as a dict.  Raises ValueError on a bad line."""
    line = line.strip()
    if not (line.startswith(f"#{GROUP} ") and line.endswith("#")):
        raise ValueError("not a #mLive statement")
    statement, _, crc = line[:-1].rpartition(" ")
    if crc32_hex(statement) != crc:
        raise ValueError(f"CRC mismatch: line says {crc}, data gives {crc32_hex(statement)}")
    payload = statement[len(f"#{GROUP} "):]
    return json.loads(payload)["Meas"]
```

Things to notice: nothing in this file knows what a battery is. It only knows a *format*. That is why it has its own test file and no dependencies.
The `ITEMS` table is the single place that defines the wire format.

</details>

## Checkpoint

Create `dev_thingset.py` in the service folder later in Part 5. For now, in a Python shell (`uv run python`):

```python
>>> from thingset import decode_live
>>> line = "paste your captured line here"
>>> decode_live(line)["rBmsState"]
3
```

Change one digit in the line and call `decode_live` again. You should get `ValueError: CRC mismatch ...`.

## Commit

```bash
git add services/bms-simulator
git commit -m "feat(bms-simulator): ThingSet #mLive encoder and decoder"
```

## Self-check questions

1. Why not use `json.dumps` to build the line?
2. If a cell voltage list had 15 entries instead of 16, would `encode_live` complain? Should it?
3. Why does the CRC protect against line noise but not against a malicious sender?

<details>
<summary>Answers</summary>

1. It would print `0.0` instead of `0.00` and add spaces, so the text (and the CRC) would not match what the firmware sends, and a strict parser could reject it.
2. No, the table does not know the array length. A stricter version could check it. In Part 6 the simulator always pads to 16 slots, so the problem cannot occur.
3. A CRC has no secret. Anyone can compute a valid one for a forged message. It detects accidental corruption only.

</details>

Next: [Part 4: The physical battery](04-battery-physics.md).
