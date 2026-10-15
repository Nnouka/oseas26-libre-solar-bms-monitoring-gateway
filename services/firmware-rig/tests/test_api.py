from fastapi.testclient import TestClient

from api import create_app
from battery import SCENARIOS, Battery
from rig import Rig
from test_rig import FakeFirmware


def client():
    fw, battery = FakeFirmware(), Battery(cells=4, seed=1)
    rig = Rig(fw, battery, interval_s=1.0, speed=60.0)
    return TestClient(create_app(rig, fw, source="tcp://rig:3333")), rig, fw, battery


def test_health_reports_the_firmware_and_the_rig_loop():
    c, rig, fw, _ = client()
    rig.tick()
    body = c.get("/health").json()
    assert body["service"] == "firmware-rig" and body["firmware_alive"] is True
    assert body["ticks"] == 1 and body["last_error"] == ""


def test_health_says_so_when_the_firmware_is_gone():
    c, _, fw, _ = client()
    fw.alive = False
    assert c.get("/health").json()["firmware_alive"] is False


def test_state_exposes_the_model_truth_next_to_the_switches():
    c, rig, _, battery = client()
    body = c.get("/state").json()
    assert body["scenario"] == "normal" and body["temp_c"] == battery.temp_c
    assert body["chg_on"] is True and body["dis_on"] is True and len(body["soc_pct"]) == 4


def test_scenario_can_be_switched_and_unknown_ones_are_refused():
    c, _, _, battery = client()
    assert c.post("/scenario/overheat").json() == {"scenario": "overheat"}
    assert battery.scenario == "overheat"
    refused = c.post("/scenario/volcano")
    assert refused.status_code == 400 and all(name in refused.json()["detail"] for name in SCENARIOS)
    assert c.post("/scenario/undervoltage").json() == {"scenario": "undervoltage"}
    assert "instant_recovery" in refused.json()["detail"]
