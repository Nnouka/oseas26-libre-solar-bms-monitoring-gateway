"""The data contract, as code. Mirrors docs/mqtt-schema.md (schema version 1)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ErrorName = Literal[
    "CELL_UNDERVOLTAGE", "CELL_OVERVOLTAGE", "SHORT_CIRCUIT", "DIS_OVERCURRENT",
    "CHG_OVERCURRENT", "OPEN_WIRE", "DIS_UNDERTEMP", "DIS_OVERTEMP", "CHG_UNDERTEMP",
    "CHG_OVERTEMP", "INT_OVERTEMP", "CELL_FAILURE", "DIS_OFF", "CHG_OFF", "FET_OVERTEMP",
]


class Meas(BaseModel):
    """Payload of bms/<id>/meas."""

    # The JSON key is "schema", but `schema` is a reserved name inside pydantic models,
    # so the Python attribute is schema_version and the alias keeps the JSON key.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(alias="schema")
    device_id: str = Field(min_length=1)
    ts: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    state: Literal["OFF", "CHG", "DIS", "NORMAL", "SHUTDOWN"]
    pack_voltage_v: float
    pack_current_a: float
    soc_pct: float = Field(ge=0, le=100)
    cell_voltages_v: list[float] = Field(min_length=1)
    cell_temps_c: list[float] = Field(min_length=1)
    mosfet_temp_c: float
    balancing_mask: int = Field(ge=0)
    errors: list[ErrorName]


class Info(BaseModel):
    """Payload of bms/<id>/info."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(alias="schema")
    device_id: str
    manufacturer: str
    model: str
    firmware: str
    chemistry: str
    cell_count: int = Field(gt=0)
    capacity_ah: float = Field(gt=0)
    limits: dict[str, float]
