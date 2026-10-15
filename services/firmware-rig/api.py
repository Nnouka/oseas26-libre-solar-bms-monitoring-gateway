"""HTTP side of the rig: health, the model's hidden truth, and the scenario switch (physics only)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from battery import SCENARIOS


def create_app(rig, firmware, source: str = "") -> FastAPI:
    app = FastAPI(title="Firmware rig")

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "firmware-rig", "firmware_alive": firmware.alive,
                "ticks": rig.ticks, "last_error": rig.last_error, "live_source": source}

    @app.get("/state")
    async def state():
        """What only the simulation knows (the BMS does not): true charge per cell, temperature, switches."""
        b = rig.battery
        return {"scenario": b.scenario, "temp_c": b.temp_c, "soc_pct": [round(x * 100.0, 1) for x in b.soc],
                "current_a": b.current_a, "chg_on": rig.chg_on, "dis_on": rig.dis_on}

    @app.post("/scenario/{name}")
    async def set_scenario(name: str):
        try:
            rig.battery.set_scenario(name)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown scenario; choose from {list(SCENARIOS)}")
        return {"scenario": name}

    return app
