from fastapi.testclient import TestClient
import main

def test_health_says_ok():
    client = TestClient(main.app)
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "bms-simulator"
    