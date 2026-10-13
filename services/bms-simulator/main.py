import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse

from physical_battery import SCENARIOS
from sim import Simulator
from mqtt_publisher import Publisher
from runner import SimRunner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
DEVICE_ID = os.getenv("DEVICE_ID", "haske-bms-001")
INTERVAL_S = float(os.getenv("PUBLISH_INTERVAL_S", "5"))
SPEED = float(os.getenv("SPEED", "60"))
SCENARIO = os.getenv("SCENARIO", "normal")
# false: the simulator behaves like the BMS alone (serial line on /live) and the WiFi gateway publishes MQTT
MQTT_ENABLED = os.getenv("MQTT_ENABLED", "true").lower() in ("1", "true", "yes")

model = Simulator(scenario=SCENARIO)
publisher = Publisher(model, MQTT_HOST, MQTT_PORT, DEVICE_ID, INTERVAL_S, SPEED) if MQTT_ENABLED else None
runner = SimRunner(model, INTERVAL_S, SPEED)


@asynccontextmanager
async def lifespan(app: FastAPI):
    (publisher or runner).start()      # runs once at startup
    yield                              # the app serves requests while we are suspended here
    (publisher or runner).stop()       # runs once at shutdown


app = FastAPI(title="BMS simulator", lifespan=lifespan)


@app.get("/")
async def root():
    return {"message": "BMS simulator", "docs": "/docs"}


@app.get("/health")
async def health():
    # "ok" even when the broker is down: the simulator itself is alive.
    return {"status": "ok", "service": "bms-simulator", "mqtt_enabled": MQTT_ENABLED,
            "mqtt_connected": publisher.connected if publisher else None}


@app.get("/state")
async def state():
    return {"device_id": DEVICE_ID, "published": publisher.published if publisher else 0,
            **model.debug(), **model.snapshot()}


@app.get("/live", response_class=PlainTextResponse)
async def live():
    """The line the real firmware sends on its serial port (ThingSet #mLive statement)."""
    return model.live_line()


@app.post("/scenario/{name}")
async def set_scenario(name: str):
    """Switch scenario live, e.g. POST /scenario/overheat during the demo."""
    try:
        model.set_scenario(name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown scenario; choose from {list(SCENARIOS)}")
    return {"scenario": name}
