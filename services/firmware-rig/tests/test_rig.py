import socket
import threading
import time

import pytest

from battery import Battery
from rig import LiveServer, Rig, parse_live

OTC = 1 << 4       # SAFETY_STATUS_B: overtemperature in charge


class FakeFirmware:
    """Records what the rig writes and answers subcommand reads from a dict."""

    def __init__(self):
        self.alive = True
        self.images: list[list[tuple[int, bytes]]] = []
        self.writes: list[tuple[int, bytes]] = []
        self.live: list[str] = []

    def write_all(self, writes):
        self.images.append(list(writes))

    def write(self, reg, payload):
        self.writes.append((reg, payload))

    def next_live_line(self, timeout=1.0):
        return self.live.pop(0) if self.live else None


def make(**kw) -> tuple[Rig, FakeFirmware, Battery]:
    fw, battery = FakeFirmware(), Battery(cells=4, seed=1)
    return Rig(fw, battery, interval_s=1.0, speed=60.0, **kw), fw, battery


def regs(writes) -> dict[int, bytes]:
    return dict(writes)


def test_boot_injects_the_batterys_first_reading_as_one_image():
    rig, fw, _ = make()
    rig.boot()
    assert len(fw.images) == 1
    assert 0x14 in regs(fw.images[0]) and 0x05 in regs(fw.images[0])      # cells and safety status


def test_tick_writes_the_new_reading_to_the_chip_as_one_image():
    """One batch per tick: settling after every single register would make a tick longer than its interval."""
    rig, fw, _ = make()
    rig.tick()
    assert fw.writes == []                                              # no register-by-register writes
    assert {0x14, 0x3A, 0x70} <= set(regs(fw.images[-1]))
    assert rig.ticks == 1


def test_chip_protection_follows_the_battery_temperature():
    rig, fw, battery = make()
    battery.temp_c = 60.0
    rig.tick()
    assert regs(fw.images[-1])[0x05][0] & OTC


def live(state: int) -> str:
    return '#mLive {"Meas":{"rBmsState":%d,"rErrorFlags":0}} 1a2b3c4d' % state


@pytest.mark.parametrize("state, chg, dis", [(0, False, False), (1, True, False), (2, False, True), (3, True, True)])
def test_the_firmwares_state_tells_the_rig_which_fets_are_on(state, chg, dis):
    rig, _, _ = make()
    rig.observe(live(state))
    assert (rig.chg_on, rig.dis_on) == (chg, dis)


def test_charging_stops_in_the_model_once_the_firmware_goes_to_dis():
    rig, _, battery = make()
    rig.observe(live(2))
    seen = []
    real_step = battery.step
    battery.step = lambda dt, chg_on, dis_on, bleed: seen.append((chg_on, dis_on)) or real_step(dt, chg_on, dis_on, bleed)
    rig.tick()
    assert seen == [(False, True)]


def test_a_garbled_line_leaves_the_switches_unchanged():
    rig, _, _ = make()
    rig.observe(live(2))
    rig.observe("#mLive {truncated")
    rig.observe("uart:~$ ")
    assert (rig.chg_on, rig.dis_on) == (False, True)


def test_parse_live_returns_the_meas_object_without_the_crc():
    assert parse_live(live(3)) == {"rBmsState": 3, "rErrorFlags": 0}
    assert parse_live("garbage") is None


def test_a_dead_firmware_stops_the_rig_instead_of_hanging():
    rig, fw, _ = make()
    fw.alive = False
    with pytest.raises(RuntimeError):
        rig.tick()


def test_live_server_sends_every_line_to_each_client():
    server = LiveServer("127.0.0.1", 0)
    server.start()
    try:
        clients = [socket.create_connection(("127.0.0.1", server.port), timeout=2) for _ in range(2)]
        time.sleep(0.2)
        server.broadcast('#mLive {"Meas":{}} 1234abcd')
        for c in clients:
            assert c.makefile("r").readline().strip() == '#mLive {"Meas":{}} 1234abcd'
    finally:
        server.stop()


def test_live_server_drops_a_vanished_client_without_stopping():
    server = LiveServer("127.0.0.1", 0)
    server.start()
    try:
        gone = socket.create_connection(("127.0.0.1", server.port), timeout=2)
        stays = socket.create_connection(("127.0.0.1", server.port), timeout=2)
        time.sleep(0.2)
        gone.close()
        for _ in range(3):
            server.broadcast("#mLive {} 00000000")
            time.sleep(0.05)
        assert stays.makefile("r").readline().startswith("#mLive")
    finally:
        server.stop()


def test_run_ticks_at_the_interval_until_stopped():
    rig, fw, _ = make()
    rig.interval_s = 0.05
    stop = threading.Event()
    thread = threading.Thread(target=rig.run, args=(stop,))
    thread.start()
    time.sleep(0.4)
    stop.set()
    thread.join(timeout=2)
    assert 4 <= rig.ticks <= 9


def test_run_records_why_it_stopped_when_the_firmware_dies():
    rig, fw, _ = make()
    rig.interval_s = 0.01
    fw.alive = False
    rig.run(threading.Event())
    assert "not running" in rig.last_error


def test_pump_feeds_every_live_line_to_the_rig_and_the_server():
    rig, fw, _ = make()
    fw.live = [live(2), live(3)]
    sent = []

    class Server:
        def broadcast(self, line):
            sent.append(line)

    stop = threading.Event()
    thread = threading.Thread(target=rig.pump, args=(stop, Server()))
    thread.start()
    time.sleep(0.3)
    stop.set()
    thread.join(timeout=2)
    assert sent == [live(2), live(3)]
    assert (rig.chg_on, rig.dis_on) == (True, True)             # the last line (NORMAL) won
