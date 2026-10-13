"""The parts of the publisher that need no broker."""
import json

from publisher import Publisher
from schema import Info, Meas
from sim import Simulator


def make() -> Publisher:
    # Creating a Publisher does not connect: connecting happens in start().
    return Publisher(Simulator(seed=1), "localhost", 1883, "dev1", interval_s=1.0, speed=60.0)


def test_topics_start_with_the_device_base():
    assert make().base == "bms/dev1"


def test_meas_json_is_valid_and_complete():
    pub = make()
    msg = json.loads(pub._meas_json())
    assert msg["schema"] == 1 and msg["device_id"] == "dev1"
    assert msg["ts"].endswith("Z")
    Meas.model_validate(msg)


def test_info_json_carries_the_limits_and_cell_count():
    info = Info.model_validate(json.loads(make()._info_json()))
    assert info.cell_count == 8
    assert info.limits["cell_overvoltage_v"] == 3.8


def test_not_connected_at_the_start():
    assert make().connected is False
