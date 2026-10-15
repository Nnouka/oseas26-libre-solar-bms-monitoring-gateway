"""Entry point: start the real firmware, inject the battery, serve #mLive over TCP and a small HTTP API."""
import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api import create_app
from battery import Battery
from firmware import FirmwareProcess
from rig import LiveServer, Rig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

BINARY = os.getenv("FIRMWARE_BINARY", "/opt/firmware/zephyr.exe")
TCP_PORT = int(os.getenv("LIVE_TCP_PORT", "3333"))
INTERVAL_S = float(os.getenv("PUBLISH_INTERVAL_S", "1"))
# Firmware time vs real time. 1 = a real device. Above 1 the chip is not populated when the firmware
# initialises its state of charge in main() (about 0.5 s of firmware time after boot), so SoC starts at 0.
SPEED = float(os.getenv("SPEED", "1"))
CELLS = int(os.getenv("CELLS", "8"))
CAPACITY_AH = float(os.getenv("CAPACITY_AH", "50"))
SCENARIO = os.getenv("SCENARIO", "normal")

firmware = FirmwareProcess.native_sim(BINARY, rt_ratio=SPEED)
battery = Battery(CELLS, CAPACITY_AH)
battery.set_scenario(SCENARIO)
rig = Rig(firmware, battery, INTERVAL_S, SPEED)
server = LiveServer("0.0.0.0", TCP_PORT)
stop = threading.Event()


@asynccontextmanager
async def lifespan(app: FastAPI):
    firmware.start()
    rig.boot()                       # the first image must reach the chip before main() reads the cells
    server.start()
    threading.Thread(target=rig.run, args=(stop,), name="rig-tick", daemon=True).start()
    threading.Thread(target=rig.pump, args=(stop, server), name="rig-pump", daemon=True).start()
    yield
    stop.set()
    server.stop()
    firmware.stop()


app = create_app(rig, firmware, source=f"tcp://0.0.0.0:{TCP_PORT}")
app.router.lifespan_context = lifespan
