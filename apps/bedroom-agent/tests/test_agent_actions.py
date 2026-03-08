from __future__ import annotations

from agent.actions import ClimatePlan, FanAction, LightAction, SpeechAction


def test_light_action_emits_light_set():
    calls = LightAction(entity_id="light.bedroom_light", state="off").to_tool_calls("cid")

    assert len(calls) == 1
    assert calls[0].tool == "light.set"
    assert calls[0].args == {"entity_id": "light.bedroom_light", "state": "off"}
    assert calls[0].correlation_id == "cid"


def test_fan_action_emits_fan_set():
    calls = FanAction(entity_id="fan.bedroom_fan", state="on").to_tool_calls("cid")

    assert len(calls) == 1
    assert calls[0].tool == "fan.set"
    assert calls[0].args == {"entity_id": "fan.bedroom_fan", "state": "on"}
    assert calls[0].correlation_id == "cid"


def test_speech_action_emits_tts_say():
    calls = SpeechAction(message="Sleep mode on.").to_tool_calls("cid")

    assert len(calls) == 1
    assert calls[0].tool == "tts.say"
    assert calls[0].args == {"message": "Sleep mode on."}
    assert calls[0].correlation_id == "cid"


def test_climate_plan_emits_ordered_climate_calls():
    calls = ClimatePlan(
        entity_id="climate.bedroom_ac",
        hvac_mode="cool",
        temperature=24,
        fan_mode="auto",
    ).to_tool_calls("cid")

    assert [call.tool for call in calls] == [
        "climate.set_mode",
        "climate.set_temperature",
        "climate.set_fan_mode",
    ]
    assert calls[0].args == {"entity_id": "climate.bedroom_ac", "hvac_mode": "cool"}
    assert calls[1].args == {"entity_id": "climate.bedroom_ac", "temperature": 24}
    assert calls[2].args == {"entity_id": "climate.bedroom_ac", "fan_mode": "auto"}


def test_climate_plan_omits_optional_calls():
    calls = ClimatePlan(
        entity_id="climate.bedroom_ac",
        hvac_mode="fan_only",
        temperature=None,
        fan_mode="low",
    ).to_tool_calls("cid")

    assert [call.tool for call in calls] == ["climate.set_mode", "climate.set_fan_mode"]
