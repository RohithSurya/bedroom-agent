from __future__ import annotations

from agent.orchestrator import Orchestrator
from core.cooldowns import CooldownStore


def _base_state() -> dict:
    return {
        "presence": True,
        "guest_mode": False,
        "room_uncomfortable": True,
        "light_entity_id": "light.bedroom_light",
        "light_state": "off",
        "fan_entity_id": "fan.bedroom_fan",
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
    assert tools[0] == "light.set"
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
    assert "fan.set" in tools
    assert "tts.say" in tools


def test_fan_on_emits_fan_set():
    orch = Orchestrator()
    out = orch.handle_request(intent="fan_on", args={}, state=_base_state())

    assert out["decision"].decision == "allow"
    assert out["actions"][0].tool == "fan.set"
    assert out["actions"][0].args["entity_id"] == "fan.bedroom_fan"
    assert out["actions"][0].args["state"] == "on"


def test_fan_off_emits_fan_set():
    orch = Orchestrator()
    out = orch.handle_request(intent="fan_off", args={}, state=_base_state())

    assert out["decision"].decision == "allow"
    assert out["actions"][0].tool == "fan.set"
    assert out["actions"][0].args["entity_id"] == "fan.bedroom_fan"
    assert out["actions"][0].args["state"] == "off"


def test_sleep_mode_accepts_light_entity_id_override():
    orch = Orchestrator()
    state = _base_state()
    state["light_state"] = "on"

    out = orch.handle_request(
        intent="sleep_mode",
        args={"light_entity_id": "light.bedlamp"},
        state=state,
    )

    assert out["actions"][0].tool == "light.set"
    assert out["actions"][0].args["entity_id"] == "light.bedlamp"


def test_focus_start_accepts_light_entity_id_override():
    orch = Orchestrator()
    state = _base_state()

    out = orch.handle_request(
        intent="focus_start",
        args={"light_entity_id": "light.bedlamp"},
        state=state,
    )

    assert out["actions"][0].tool == "light.set"
    assert out["actions"][0].args["entity_id"] == "light.bedlamp"


def test_night_mode_cooldown_omits_cooldown_safety_check():
    orch = Orchestrator(cooldowns=CooldownStore())
    state = {"presence": True, "guest_mode": False}

    first = orch.handle_request(intent="night_mode", args={}, state=state)
    orch.cooldowns.mark_ran(first["cooldown_key"], int(first["cooldown_seconds"]))

    out = orch.handle_request(intent="night_mode", args={}, state=state)

    assert out["decision"].decision == "deny"
    assert out["decision"].reason.startswith("cooldown_active:")
    assert "cooldown" not in out["decision"].safety_checks


def test_fan_on_cooldown_adds_cooldown_safety_check():
    orch = Orchestrator(cooldowns=CooldownStore())
    state = _base_state()

    first = orch.handle_request(intent="fan_on", args={}, state=state)
    orch.cooldowns.mark_ran(first["cooldown_key"], int(first["cooldown_seconds"]))

    out = orch.handle_request(intent="fan_on", args={}, state=state)

    assert out["decision"].decision == "deny"
    assert out["decision"].reason.startswith("cooldown_active:")
    assert "cooldown" in out["decision"].safety_checks
