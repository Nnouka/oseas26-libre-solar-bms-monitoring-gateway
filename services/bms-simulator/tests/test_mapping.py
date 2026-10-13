from chip import ChipReading
from firmware import ERR, Firmware
from mapping import error_names, to_mqtt
from schema import Meas


def test_error_names_decode_the_bit_mask():
    assert error_names(0) == []
    assert error_names(ERR["CELL_UNDERVOLTAGE"] | ERR["CHG_OVERTEMP"]) == ["CELL_UNDERVOLTAGE", "CHG_OVERTEMP"]


def test_unconnected_channels_are_dropped_and_the_result_matches_the_schema():
    cells = [3.31, 3.30, 3.32] + [0.0] * 13
    r = ChipReading(cells, 4.2, [25.0, 24.5], 27.0, 26.0)
    fw = Firmware()
    fw.boot(r)
    fw.update(r, 1000)
    msg = to_mqtt(fw.meas())
    assert msg["cell_voltages_v"] == [3.31, 3.3, 3.32]
    assert msg["state"] in {"DIS", "NORMAL"}
    Meas.model_validate({"schema": 1, "device_id": "t", "ts": "2026-01-01T00:00:00Z", **msg})
