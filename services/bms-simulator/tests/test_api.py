from fastapi.testclient import TestClient

import main


def test_health_is_ok_even_without_a_broker():
    # no `with` block => the lifespan (and so the MQTT thread) is not started
    client = TestClient(main.app)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "bms-simulator"
    assert body["mqtt_connected"] is False


def test_live_endpoint_returns_a_valid_line():
    from thingset import decode_live
    client = TestClient(main.app)
    assert "rSOC_pct" in decode_live(client.get("/live").text)


def test_unknown_scenario_returns_400():
    client = TestClient(main.app)
    assert client.post("/scenario/bogus").status_code == 400
    assert client.post("/scenario/overheat").json() == {"scenario": "overheat"}
    client.post("/scenario/normal")


def test_without_mqtt_the_simulator_still_runs_and_serves_the_live_line(monkeypatch):
    import importlib

    monkeypatch.setenv("MQTT_ENABLED", "false")
    nomqtt = importlib.reload(main)
    try:
        with TestClient(nomqtt.app) as client:           # the lifespan runs: the SimRunner starts
            body = client.get("/health").json()
            assert body["mqtt_enabled"] is False and body["mqtt_connected"] is None
            assert client.get("/live").text.startswith("#mLive")
    finally:
        monkeypatch.delenv("MQTT_ENABLED")
        importlib.reload(main)
