"""Health score, cycle counting and fault events derived from raw BMS `meas` messages (docs/mqtt-schema.md).

Pure Python, no MQTT and no I/O. Used by services/bms-bridge.
"""
from .logic import FAULTS, DeviceTracker, assess_health, fault_info

__all__ = ["FAULTS", "DeviceTracker", "assess_health", "fault_info"]
