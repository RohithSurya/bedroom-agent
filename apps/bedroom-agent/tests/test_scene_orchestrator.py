from __future__ import annotations

from agent.orchestrator import Orchestrator


def _base_state() -> dict:
    return {
        "presence": True,
        "guest_mode": False,
        "room_uncomfortable": True,
        "light_entity_id": "switch.bedroom_light_switch",
        "light_state": "off",
        "fan_entity_id": "switch.bedroom_fan_plug",
        "fan_state": "off",
        "ac_entity_id": "climate.bedroom_ac",
        "ac_available": True,
        "comfort_target_temp_c": 24,
        "sleep_target_temp_c": 24,
        "focus_mode_enable_fan": True,
        "focus_mode_enable_climate": True,
        "sleep_mode_enable_climate": True,
        "comfort_use_fan_fallback": True,
        "temperature_c": 27.0,
        "humidity_pct": 68.0,
    }


def test_comfort_adjust_emits_cooling_actions():
    orch = Orchestrator()
    out = orch.handle_request(intent="comfort_adjust", args={}, state=_base_state())

    tools = [call.tool for call in out["actions"]]
    assert out["decision"].decision == "allow"
    assert tools == ["climate.set_mode", "climate.set_temperature", "climate.set_fan_mode"]


def test_sleep_mode_turns_off_light_and_cools():
    orch = Orchestrator()
    state = _base_state()
    state["light_state"] = "on"
    out = orch.handle_request(intent="sleep_mode", args={}, state=state)

    tools = [call.tool for call in out["actions"]]
    assert out["decision"].decision == "allow"
    assert tools[0] == "switch.set"
    assert tools[1:] == [
        "climate.set_mode",
        "climate.set_temperature",
        "climate.set_fan_mode",
        "tts.say",
    ]


def test_focus_start_uses_fan_fallback_without_ac():
    orch = Orchestrator()
    state = _base_state()
    state["ac_available"] = False
    out = orch.handle_request(intent="focus_start", args={}, state=state)

    tools = [call.tool for call in out["actions"]]
    assert out["decision"].decision == "allow"
    assert "switch.set" in tools
    assert "tts.say" in tools
