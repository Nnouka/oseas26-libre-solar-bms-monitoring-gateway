"""Against the REAL native_sim firmware. Needs RIG_BINARY (the zephyr.exe built with rig.conf); skipped otherwise.

    wsl: RIG_BINARY=~/spike/build/zephyr/zephyr.exe uv run --python 3.12 --no-project --with pytest python -m pytest tests
"""
import json
import os
import threading
import time

import pytest

from chip import MAX_CELLS, ChipReading
from encoder import encode, encode_safety
from firmware import FirmwareProcess
from protections import ChipProtections

BINARY = os.environ.get("RIG_BINARY")
pytestmark = pytest.mark.skipif(not BINARY, reason="RIG_BINARY not set")


def wait_for(condition, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.25)
    return False


def meas_of(line: str) -> dict:
    body = line.split(" ", 1)[1].rsplit(" ", 1)[0]          # drop the trailing CRC
    return json.loads(body)["Meas"]


@pytest.fixture(scope="module")
def fw():
    process = FirmwareProcess.native_sim(os.path.expanduser(BINARY), rt_ratio=1.0)
    process.start()
    yield process
    process.stop()


PROTECTIONS = ChipProtections()


def inject(fw, **kw):
    base = dict(cell_v=[3.300, 3.302, 3.301, 3.299] + [0.0] * (MAX_CELLS - 4), current_a=1.0,
                cell_temps_c=[25.0, 24.0], ic_temp_c=27.0, mosfet_temp_c=29.0)
    reading = ChipReading(**{**base, **kw})
    for reg, payload in encode(reading) + encode_safety(PROTECTIONS.update(reading)):
        fw.write(reg, payload)


def fresh_meas(fw, wait_lines=3):
    for _ in range(wait_lines):
        line = fw.next_live_line(timeout=5.0)
    assert line is not None, "no #mLive line from the real firmware"
    return meas_of(line)


def test_the_firmware_reports_the_injected_cells_and_current(fw):
    inject(fw)
    m = fresh_meas(fw)
    assert m["rCellVoltages_V"][:4] == [3.3, 3.302, 3.301, 3.299]
    assert m["rPackCurrent_A"] == 1.0
    assert m["rPackVoltage_V"] == pytest.approx(13.2, abs=0.01)


def test_the_firmware_reports_injected_temperatures(fw):
    inject(fw, cell_temps_c=[31.0, 30.0], mosfet_temp_c=33.0, ic_temp_c=35.0)
    m = fresh_meas(fw)
    assert m["rCellTemps_degC"][0] == pytest.approx(31.0, abs=0.2)
    assert m["rMOSFETTemp_degC"] == pytest.approx(33.0, abs=0.2)
    assert m["rICTemp_degC"] == pytest.approx(35.0, abs=0.2)


def test_an_overheated_battery_makes_the_firmware_raise_an_error_flag(fw):
    inject(fw, cell_temps_c=[60.0, 60.0], mosfet_temp_c=60.0)
    m = fresh_meas(fw, wait_lines=6)
    assert m["rErrorFlags"] & (1 << 9)            # CHG_OVERTEMP (bit 9 in ERROR_BITS)


def test_soc_is_initialised_when_the_first_image_is_injected_during_boot():
    """The firmware sets SoC once, from the cells, in main(): the chip must not be empty by then."""
    process = FirmwareProcess.native_sim(os.path.expanduser(BINARY), rt_ratio=1.0)
    process.start()
    try:
        reading = ChipReading(cell_v=[3.265] * 4 + [0.0] * (MAX_CELLS - 4), current_a=0.0,
                              cell_temps_c=[25.0, 25.0], ic_temp_c=27.0, mosfet_temp_c=29.0)
        process.write_all(encode(reading) + encode_safety(ChipProtections().update(reading)))
        m = fresh_meas(process, wait_lines=3)
        assert 40 < m["rSOC_pct"] < 60            # 3.265 V per cell is about 50 % on the LFP curve
    finally:
        process.stop()


def test_closed_loop_the_firmware_switches_charging_off_and_the_rig_sees_it():
    from battery import Battery
    from rig import Rig

    process = FirmwareProcess.native_sim(os.path.expanduser(BINARY), rt_ratio=1.0)
    process.start()
    try:
        battery = Battery(cells=8, seed=1)
        rig = Rig(process, battery, interval_s=1.0, speed=1.0)
        rig.boot()
        stop = threading.Event()
        threading.Thread(target=rig.run, args=(stop,), daemon=True).start()

        class Sink:
            def broadcast(self, line):
                pass

        threading.Thread(target=rig.pump, args=(stop, Sink()), daemon=True).start()
        battery.temp_c = 55.0                        # far above the 45 C charge limit
        assert wait_for(lambda: rig.chg_on is False), "firmware never switched charging off"
        battery.temp_c = 25.0                        # cooled down: past the hysteresis charging is allowed again
        assert wait_for(lambda: rig.chg_on is True), "firmware never switched charging back on"
        stop.set()
    finally:
        process.stop()
