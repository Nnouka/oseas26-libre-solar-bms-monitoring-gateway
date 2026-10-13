import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from battery import SCENARIOS, BatteryModel
from publisher import Publisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DEVICE_ID = os.getenv("DEVICE_ID", "haske-bms-001")
INTERVAL_S = float(os.getenv("PUBLISH_INTERVAL_S", "5"))
SPEED = float(os.getenv("SPEED", "60"))
SCENARIO = os.getenv("SCENARIO", "normal")

model = BatteryModel(scenario=SCENARIO)
publisher = Publisher(model, MQTT_HOST, MQTT_PORT, DEVICE_ID, INTERVAL_S, SPEED)


@asynccontextmanager
async def lifespan(app: FastAPI):
    publisher.start()
    yield
    publisher.stop()


app = FastAPI(title="BMS simulator", lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "BMS simulator", "docs": "/docs"}


@app.get("/health")
async def health():
    # Stays "ok" even when the broker is down: the simulator itself is alive.
    return {"status": "ok", "service": "bms-simulator", "mqtt_connected": publisher.connected}


@app.get("/state")
async def state():
    return {"device_id": DEVICE_ID, "published": publisher.published, **model.debug(), **model.snapshot()}


@app.post("/scenario/{name}")
async def set_scenario(name: str):
    """Switch scenario live, e.g. POST /scenario/overheat during the demo."""
    try:
        model.set_scenario(name)
    except ValueError:
        raise HTTPException(400, f"unknown scenario; choose from {list(SCENARIOS)}")
    return {"scenario": name}
