from __future__ import annotations

from types import SimpleNamespace

from app import AgentAppState
from memory.sqlite_kv import SqliteKV
from memory.tiered_memory import TieredMemory


def test_record_episode_persists_recent_episode(tmp_path):
    agent = AgentAppState.__new__(AgentAppState)
    agent.settings = SimpleNamespace()
    agent.kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    agent.memory = TieredMemory(kv=agent.kv)

    state = {
        "presence": True,
        "temperature_c": 27.0,
        "humidity_pct": 66.0,
        "light_state": "on",
        "fan_state": "off",
        "ac_state": "cool",
        "ac_hvac_mode": "cool",
        "vision": {"available": False},
        "relevant_prefs": {"sleep.preferred_temp_c": 26},
    }

    decision = {"decision": "allow", "reason": "ok"}
    actions = [
        {"tool": "light.set", "args": {"entity_id": "light.bedroom_light", "state": "off"}},
        {"tool": "tts.say", "args": {"message": "Sleep mode on."}},
    ]
    execution = {"success": True, "failures": [], "executed_tools": ["light.set", "tts.say"]}

    episode = agent.record_episode(
        user_text="I'm winding down",
        intent="sleep_mode",
        state=state,
        decision=decision,
        actions=actions,
        execution=execution,
        memory_hits=["sleep.preferred_temp_c"],
    )

    assert episode["intent"] == "sleep_mode"
    assert episode["execution_success"] is True
    assert episode["memory_hits"] == ["sleep.preferred_temp_c"]

    recent = agent.memory.get_recent_episodes()
    assert len(recent) == 1
    assert recent[0]["intent"] == "sleep_mode"

    summary = agent.memory.get_rolling_summary()
    assert "Most common intent: sleep_mode" in summary
