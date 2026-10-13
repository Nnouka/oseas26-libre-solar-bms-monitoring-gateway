"""The most important test of the project: we print exactly what the real firmware prints."""
from chip import ChipReading
from firmware import Firmware
from test_thingset import NATIVE_SIM_LINE
from thingset import encode_live


def test_our_firmware_prints_the_same_line_as_native_sim():
    fw = Firmware()
    fw.boot(ChipReading.empty())
    for k in range(3):                       # OFF -> DIS -> NORMAL needs two passes
        fw.update(ChipReading.empty(), now_ms=1000.0 * k)
    assert encode_live(fw.meas()) == NATIVE_SIM_LINE
