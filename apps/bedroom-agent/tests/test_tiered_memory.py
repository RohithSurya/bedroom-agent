from __future__ import annotations

from memory.sqlite_kv import SqliteKV
from memory.tiered_memory import TieredMemory


def test_tiered_memory_caps_recent_episodes(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    memory = TieredMemory(kv=kv, max_recent_episodes=3)

    for i in range(5):
        memory.record_episode(
            {
                "ts": i,
                "user_text": f"request {i}",
                "intent": "sleep_mode",
                "plan_summary": ["light off"],
                "policy_decision": "allow",
                "execution_success": True,
            }
        )

    recent = memory.get_recent_episodes()
    assert len(recent) == 3
    assert recent[0]["ts"] == 4
    assert recent[1]["ts"] == 3
    assert recent[2]["ts"] == 2


def test_tiered_memory_builds_summary(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    memory = TieredMemory(kv=kv)

    memory.record_episode(
        {
            "ts": 1,
            "user_text": "wind down",
            "intent": "sleep_mode",
            "plan_summary": ["light off", "fan low"],
            "policy_decision": "allow",
            "execution_success": True,
        }
    )

    summary = memory.get_rolling_summary()
    assert "Recent episodes: 1 total" in summary
    assert "Most common intent: sleep_mode" in summary
    assert "policy=allow" in summary


def test_tiered_memory_returns_relevant_preferences(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    kv.set("prefs", "sleep.preferred_temp_c", 24)
    kv.set("prefs", "sleep.prefer_lights_off", True)
    kv.set("prefs", "focus.prefer_fan", True)

    memory = TieredMemory(kv=kv)

    prefs = memory.get_relevant_preferences(intent="sleep_mode", user_text="help me wind down")

    assert prefs == {
        "sleep.preferred_temp_c": 24,
        "sleep.prefer_lights_off": True,
    }
