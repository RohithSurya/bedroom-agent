from __future__ import annotations

from agent.decision_engine import DecisionEngine
from memory.sqlite_kv import SqliteKV


class FakeLLM:
    def __init__(self, response: dict):
        self.response = response

    def generate_json(self, *, prompt, schema=None, temperature=0.0):
        return self.response


class UnusedLLM:
    def generate_json(self, *, prompt, schema=None, temperature=0.0):
        raise AssertionError("LLM should not be called for deterministic mode requests")


def _base_state() -> dict:
    return {
        "presence": True,
        "door_open": False,
        "guest_mode": False,
        "temperature_entity_id": "sensor.temp_humidity_sensor_temperature",
        "temperature_c": 27.4,
        "humidity_entity_id": "sensor.temp_humidity_sensor_humidity",
        "humidity_pct": 68.0,
        "ac_entity_id": "climate.bedroom_ac",
        "ac_available": True,
        "ac_state": "off",
        "ac_hvac_mode": "off",
        "ac_target_temp_c": 30,
        "ac_fan_mode": "low",
        "light_state": "off",
        "fan_state": "off",
        "comfort_trigger_temp_c": 26.0,
        "comfort_trigger_humidity_pct": 65.0,
        "room_uncomfortable": True,
        "vision": {"available": True, "bed_state": "partial", "desk_state": "active"},
    }


def test_decision_engine_accepts_valid_llm_choice(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    kv.append_event("enter_detected", {"quiet_hours": False})
    engine = DecisionEngine(
        kv=kv,
        llm=FakeLLM(
            {
                "intent": "comfort_adjust",
                "args": {},
                "confidence": 0.91,
                "rationale": "The room is occupied and warm.",
                "reasoning_tags": ["presence_true", "temp_high"],
            }
        ),
    )

    choice = engine.choose_intent(
        source="user_chat",
        trigger="chat_request",
        user_text="What should happen now?",
        state=_base_state(),
    )

    assert choice.intent == "comfort_adjust"
    assert choice.fallback_used is False


def test_decision_engine_short_circuits_explicit_focus_request(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    engine = DecisionEngine(kv=kv, llm=UnusedLLM())

    choice = engine.choose_intent(
        source="user_chat",
        trigger="chat_request",
        user_text="Start focus mode",
        state=_base_state(),
    )

    assert choice.intent == "focus_start"
    assert choice.fallback_used is False
    assert "deterministic" in choice.reasoning_tags


def test_decision_engine_falls_back_on_low_confidence(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    engine = DecisionEngine(
        kv=kv,
        llm=FakeLLM(
            {
                "intent": "comfort_adjust",
                "args": {},
                "confidence": 0.2,
                "rationale": "Weak guess.",
                "reasoning_tags": ["low_confidence"],
            }
        ),
    )

    choice = engine.choose_intent(
        source="user_chat",
        trigger="chat_request",
        user_text="What should happen now?",
        state=_base_state(),
    )

    assert choice.intent == "no_action"
    assert choice.fallback_used is True


def test_decision_engine_converts_comfort_adjust_to_no_action_when_comfortable(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    engine = DecisionEngine(
        kv=kv,
        llm=FakeLLM(
            {
                "intent": "comfort_adjust",
                "args": {},
                "confidence": 0.95,
                "rationale": "Cooling seems appropriate.",
                "reasoning_tags": ["temperature", "comfort"],
            }
        ),
    )
    state = _base_state()
    state["temperature_c"] = 25.17
    state["humidity_pct"] = 28.66
    state["room_uncomfortable"] = False

    choice = engine.choose_intent(
        source="user_chat",
        trigger="chat_request",
        user_text="What should happen now?",
        state=state,
    )

    assert choice.intent == "no_action"
    assert "thresholds" in choice.reasoning_tags
    assert "26" in choice.rationale
    assert "65" in choice.rationale


def test_decision_engine_overrides_model_rationale_for_comfort_no_action(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    engine = DecisionEngine(
        kv=kv,
        llm=FakeLLM(
            {
                "intent": "no_action",
                "args": {},
                "confidence": 0.95,
                "rationale": "Current temperature is above ideal room temperature (25.06°C) and humidity is low.",
                "reasoning_tags": ["room_uncomfortable", "thresholds", "no_energy_needed"],
            }
        ),
    )
    state = _base_state()
    state["temperature_c"] = 25.06
    state["humidity_pct"] = 28.53
    state["room_uncomfortable"] = False

    choice = engine.choose_intent(
        source="user_chat",
        trigger="chat_request",
        user_text="Make the room comfortable",
        state=state,
    )

    assert choice.intent == "no_action"
    assert choice.reasoning_tags == ["room_comfortable", "thresholds", "no_energy_needed"]
    assert "below the cooling threshold (26" in choice.rationale
    assert "below the comfort threshold (65" in choice.rationale
