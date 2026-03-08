from __future__ import annotations

from types import SimpleNamespace

from app import AgentAppState
from agent.runner import Runner
from tools.tool_executor import ToolExecutor


class _FakeKV:
    def get_namespace(self, namespace: str) -> dict:
        return {}

    def get(self, namespace: str, key: str, default=None):
        return default


def test_build_runtime_state_includes_bedroom_lamp():
    executor = ToolExecutor(mode="active")
    executor.device_state["lights"]["light.bedlamp"]["state"] = "on"

    agent = AgentAppState.__new__(AgentAppState)
    agent.settings = SimpleNamespace(
        ENTRY_LIGHT_ENTITY_ID="light.bedroom_light",
        BEDROOM_LAMP_ENTITY_ID="light.bedlamp",
        BEDROOM_FAN_ENTITY_ID="fan.bedroom_fan",
        BEDROOM_AC_ENTITY_ID="climate.bedroom_ac",
        TEMP_SENSOR_ENTITY_ID="sensor.temp_humidity_sensor_temperature",
        HUMIDITY_SENSOR_ENTITY_ID="sensor.temp_humidity_sensor_humidity",
        COMFORT_TRIGGER_TEMP_C=25.0,
        COMFORT_TRIGGER_HUMIDITY_PCT=65.0,
        COMFORT_TARGET_TEMP_C=24,
        SLEEP_TARGET_TEMP_C=27,
        FOCUS_MODE_ENABLE_FAN=True,
        FOCUS_MODE_ENABLE_CLIMATE=True,
        SLEEP_MODE_ENABLE_CLIMATE=True,
        COMFORT_USE_FAN_FALLBACK=True,
    )
    agent.kv = _FakeKV()
    agent.runner = Runner(executor=executor)

    state = agent.build_runtime_state(intent="focus_start")

    assert state["bedroom_lamp_entity_id"] == "light.bedlamp"
    assert state["bedroom_lamp_state"] == "on"
    assert "light.bedlamp" in state["_metrics"]["required_ids"]
