import sys
import time
from pathlib import Path

import pytest

from firmware import FirmwareDead, FirmwareProcess

FAKE = str(Path(__file__).parent / "fake_firmware.py")


@pytest.fixture
def fw(tmp_path):
    uart1 = tmp_path / "uart1.txt"
    uart1.write_text("")
    process = FirmwareProcess([sys.executable, FAKE, str(uart1)])
    process.start()
    yield process
    process.stop()


def test_uart1_path_is_discovered_from_the_boot_log(fw, tmp_path):
    assert fw.uart1_path == str(tmp_path / "uart1.txt")


def test_written_registers_can_be_read_back(fw):
    fw.write(0x14, bytes.fromhex("e40c"))
    assert fw.read_direct(0x14, 2) == bytes.fromhex("e40c")


def test_a_write_longer_than_the_shell_buffer_arrives_complete(fw):
    payload = bytes(range(1, 33))
    fw.write(0x14, payload)
    assert fw.read_direct(0x14, 32) == payload


def test_live_lines_from_uart1_are_queued(fw):
    fw.shell('fake live #mLive {"Meas":{}} 1a2b3c4d\n')
    assert fw.next_live_line(timeout=2.0) == '#mLive {"Meas":{}} 1a2b3c4d'


def test_a_dead_process_raises_instead_of_hanging(fw):
    with pytest.raises(FirmwareDead):
        fw.shell("fake exit")
        time.sleep(0.3)
        fw.shell("i2c read i2c@100 0x8 0x14 2", timeout=1.0)
    assert not fw.alive


def test_a_sixteen_byte_read_spanning_the_hexdump_gap_is_parsed(fw):
    payload = bytes(range(1, 16))
    fw.write(0x14, payload)
    fw.write(0x23, b"")
    assert fw.read_direct(0x14, 16) == payload + b""


def test_write_all_applies_every_register_image(fw):
    fw.write_all([(0x14, bytes([1, 2, 3])), (0x3A, bytes.fromhex("6400")), (0x70, bytes.fromhex("2c0b"))])
    assert fw.read_direct(0x14, 3) == bytes([1, 2, 3])
    assert fw.read_direct(0x3A, 2) == bytes.fromhex("6400")
    assert fw.read_direct(0x70, 2) == bytes.fromhex("2c0b")
