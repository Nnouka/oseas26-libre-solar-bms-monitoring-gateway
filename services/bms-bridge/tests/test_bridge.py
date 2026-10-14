import pytest

pytest.importorskip("paho.mqtt.client")


def test_the_bridge_module_imports_with_its_dependencies():
    """The bridge was shipped unable to start (`import discovery`, but the file is ha_discovery.py)."""
    import bridge

    assert bridge.Bridge


def test_the_bridge_announces_home_assistant_entities_for_a_device():
    import ha_discovery

    topics = [topic for topic, _payload in ha_discovery.messages("haske-bms-001")]
    assert topics and all(t.startswith("homeassistant/") for t in topics)
