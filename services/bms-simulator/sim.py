"""Battery + firmware, glued together.  This is the object the publisher and the API talk to."""
from __future__ import annotations

import threading

import firmware as fwmod
import mapping
import thingset
from battery import SCENARIOS, Battery
from firmware import Config, Firmware

MAX_CHUNK_S = 30.0     # one firmware loop iteration per <= 30 simulated seconds


class Simulator:
    def __init__(self, cells: int = 8, capacity_ah: float = 50.0, start_hour: float = 8.0,
                 seed: int | None = None, scenario: str = "normal"):
        self.battery = Battery(cells, capacity_ah, start_hour=start_hour, seed=seed)
        self.fw = Firmware(Config(capacity_ah=capacity_ah))
        self.n = cells
        self.capacity_ah = capacity_ah
        self._lock = threading.Lock()          # publisher thread and web API share this object
        self.now_ms = 0.0
        self.fw.boot(self.battery.read_chip())
        self.set_scenario(scenario)

    def set_scenario(self, name: str) -> None:
        with self._lock:
            self.battery.set_scenario(name)

    @property
    def scenario(self) -> str:
        return self.battery.scenario

    def step(self, dt_sim_s: float) -> None:
        with self._lock:
            while dt_sim_s > 0:
                chunk = min(MAX_CHUNK_S, dt_sim_s)
                reading = self.battery.step(chunk, self.fw.chg_on, self.fw.dis_on,
                                            self.fw.balancing_mask)
                self.now_ms += chunk * 1000.0
                self.fw.update(reading, self.now_ms)
                dt_sim_s -= chunk

    # ------------------------------------------------------------------ outputs
    def live_line(self) -> str:
        """The line the real firmware would send on its serial port."""
        with self._lock:
            return thingset.encode_live(self.fw.meas())

    def snapshot(self) -> dict:
        """The MQTT `meas` message body."""
        with self._lock:
            return mapping.to_mqtt(self.fw.meas())

    @staticmethod
    def limits_for(cfg: Config) -> dict[str, float]:
        return {
            "cell_overvoltage_v": cfg.cell_ov, "cell_undervoltage_v": cfg.cell_uv,
            "chg_max_temp_c": cfg.chg_ot, "dis_max_temp_c": cfg.dis_ot,
            "temp_hysteresis_c": cfg.temp_hyst, "cell_failure_diff_v": cfg.cell_failure_diff_v,
            "chg_overcurrent_a": cfg.chg_oc, "dis_overcurrent_a": cfg.dis_oc,
        }

    def limits(self) -> dict[str, float]:
        return self.limits_for(self.fw.cfg)

    def debug(self) -> dict:
        """Values that only exist inside the simulation (the BMS does not know them)."""
        with self._lock:
            b = self.battery
            true_soc = 100.0 * sum(b.soc) / b.n
            return {
                "scenario": b.scenario,
                "sim_hour": round(b.hour_of_day(), 2),
                "ambient_c": round(b.ambient_c, 1),
                "true_soc_pct": round(true_soc, 1),
                "soc_estimate_error_pct": round(self.fw.soc - true_soc, 1),
            }
