import pytest
from fastapi.testclient import TestClient

from mapping import NoCells, to_mqtt
from relay import Relay
from schema import Meas
from test_thingset import NATIVE_SIM_LINE
from thingset import decode_live, encode_live

GOOD = {
    "rPackVoltage_V": 26.16, "rStackVoltage_V": 26.16, "rPackCurrent_A": 3.2,
    "rCellTemps_degC": [29.0, 28.4], "rICTemp_degC": 31.0, "rMOSFETTemp_degC": 29.0,
    "rSOC_pct": 57.7, "rErrorFlags": 1 << 9, "rBmsState": 3,
    "rCellVoltages_V": [3.269, 3.271, 3.27, 3.268, 3.268, 3.27, 3.271, 3.269] + [0.0] * 8,
    "rCellAvgVoltage_V": 3.27, "rCellMinVoltage_V": 3.268, "rCellMaxVoltage_V": 3.271,
    "rBalancingStatus": 0,
}


def relay(**kw) -> Relay:
    ticks = iter(range(0, 10_000, 5))                   # a fake clock that advances 5 s per call
    return Relay(iter(()), "localhost", 1883, "dev1", clock=lambda: float(next(ticks)), **kw)


def test_native_sim_empty_chip_is_recognised_not_published():
    r = relay()
    assert r.process_line(NATIVE_SIM_LINE) is None
    assert r.stats["no_cells"] == 1 and r.stats["bad_crc"] == 0
    with pytest.raises(NoCells):
        to_mqtt(decode_live(NATIVE_SIM_LINE))


def test_a_corrupted_line_is_counted_and_dropped():
    r = relay()
    assert r.process_line(encode_live(GOOD).replace("57.7", "57.8")) is None
    assert r.stats["bad_crc"] == 1


def test_shell_noise_is_ignored_silently():
    r = relay()
    assert r.process_line("uart:~$ ?Meas") is None
    assert r.stats["lines"] == 0


def test_a_good_line_becomes_a_valid_schema_message():
    msg = relay().process_line(encode_live(GOOD))
    assert msg is not None
    Meas.model_validate(msg)
    assert msg["errors"] == ["CHG_OVERTEMP"] and len(msg["cell_voltages_v"]) == 8


def test_nothing_is_published_while_not_connected_but_the_cell_count_is_learned():
    r = relay()
    assert r.handle(encode_live(GOOD)) is False
    assert r.cell_count == 8


def test_publishes_to_the_meas_topic_when_connected():
    r = relay(min_interval_s=5.0)
    r.connected = True
    sent = []
    r.client.publish = lambda topic, *a, **k: sent.append(topic)   # no network in a unit test
    r._announce = lambda: None
    assert r.handle(encode_live(GOOD)) is True
    assert sent == ["bms/dev1/mpm", "bms/dev1/meas"]       # the first sample also goes to MPM


def test_mpm_hears_about_changes_not_about_every_line():
    r = relay(min_interval_s=100.0)
    r.connected = True
    sent = []
    r.client.publish = lambda topic, *a, **k: sent.append(topic)
    r._announce = lambda: None
    for _ in range(3):
        r.handle(encode_live(GOOD))
    assert sent.count("bms/dev1/mpm") == 1                  # "first"; nothing changed after that
    r.handle(encode_live({**GOOD, "rErrorFlags": 0}))       # the fault clears
    assert sent.count("bms/dev1/mpm") == 2


def test_publishing_is_rate_limited():
    r = relay(min_interval_s=100.0)          # the fake clock moves 5 s per call
    r.connected = True
    r.client.publish = lambda *a, **k: None
    r._announce = lambda: None
    line = encode_live(GOOD)
    assert [r.handle(line) for _ in range(3)] == [True, False, False]


def test_wifi_down_stops_publishing():
    r = relay()
    r.connected = True
    r.wifi_up = False
    assert r.handle(encode_live(GOOD)) is False


def test_api_health_and_bad_wifi_state():
    import main
    c = TestClient(main.app)
    assert c.get("/health").json()["service"] == "wifi-sim"
    assert c.post("/wifi/sideways").status_code == 400
