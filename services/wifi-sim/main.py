import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from relay import Relay
from source import open_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

SOURCE = os.getenv("SOURCE", "http://localhost:8000/live")   # serial:COM5 | tcp://host:port | file:/path | http://.../live
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DEVICE_ID = os.getenv("DEVICE_ID", "haske-bms-001")
PUBLISH_INTERVAL_S = float(os.getenv("PUBLISH_INTERVAL_S", "5"))
CAPACITY_AH = float(os.getenv("CAPACITY_AH", "50"))

relay = Relay(open_source(SOURCE), MQTT_HOST, MQTT_PORT, DEVICE_ID, PUBLISH_INTERVAL_S, CAPACITY_AH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    relay.start()
    yield
    relay.stop()


app = FastAPI(title="WiFi gateway simulator", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "wifi-sim", "mqtt_connected": relay.connected,
            "wifi_up": relay.wifi_up, "source": SOURCE}


@app.get("/stats")
async def stats():
    return {**relay.stats, "last_error": relay.last_error, "cell_count": relay.cell_count}


@app.post("/wifi/{state}")
async def wifi(state: str):
    if state not in ("up", "down"):
        raise HTTPException(status_code=400, detail="use /wifi/up or /wifi/down")
    relay.set_wifi(state == "up")
    return {"wifi_up": relay.wifi_up}
