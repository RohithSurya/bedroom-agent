from __future__ import annotations

from typing import Any, Optional

from memory.reflection import EpisodeReflector, ALLOWED_PREF_KEYS
from memory.sqlite_kv import SqliteKV


class FakeLLM:
    """Minimal LLM stub that returns a canned JSON response."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def generate_json(
        self,
        *,
        prompt: str,
        schema: Optional[dict[str, Any]] = None,
        images_b64: Optional[list[str]] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
    ) -> dict[str, Any]:
        return self._response


class ExplodingLLM:
    """LLM stub that always raises."""

    def generate_json(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("LLM exploded")


def _make_episode(intent: str = "sleep_mode") -> dict[str, Any]:
    return {
        "intent": intent,
        "plan_summary": ["light.set", "climate.set_mode", "tts.say"],
    }


def test_reflect_extracts_preferences(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    llm = FakeLLM(
        {
            "preferences": [
                {
                    "key": "sleep.preferred_temp_c",
                    "value": 22,
                    "reason": "User said it was too cold",
                },
            ]
        }
    )
    reflector = EpisodeReflector(kv=kv, llm=llm)

    updates = reflector.reflect(_make_episode(), "it was too cold last night")

    assert len(updates) == 1
    assert updates[0]["key"] == "sleep.preferred_temp_c"
    assert updates[0]["value"] == 22

    # Verify KV was written
    assert kv.get("prefs", "sleep.preferred_temp_c", None) == 22


def test_reflect_multiple_preferences(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    llm = FakeLLM(
        {
            "preferences": [
                {"key": "sleep.preferred_temp_c", "value": 23, "reason": "too warm"},
                {"key": "comfort.prefer_fan", "value": True, "reason": "likes airflow"},
            ]
        }
    )
    reflector = EpisodeReflector(kv=kv, llm=llm)

    updates = reflector.reflect(_make_episode(), "too warm, turn on the fan")

    assert len(updates) == 2
    assert kv.get("prefs", "sleep.preferred_temp_c", None) == 23
    assert kv.get("prefs", "comfort.prefer_fan", None) is True


def test_reflect_filters_disallowed_keys(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    llm = FakeLLM(
        {
            "preferences": [
                {"key": "evil.hack_system", "value": True, "reason": "injected"},
                {"key": "sleep.preferred_temp_c", "value": 25, "reason": "valid"},
            ]
        }
    )
    reflector = EpisodeReflector(kv=kv, llm=llm)

    updates = reflector.reflect(_make_episode(), "something")

    assert len(updates) == 1
    assert updates[0]["key"] == "sleep.preferred_temp_c"
    assert kv.get("prefs", "evil.hack_system", "MISSING") == "MISSING"


def test_reflect_no_preferences(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    llm = FakeLLM({"preferences": []})
    reflector = EpisodeReflector(kv=kv, llm=llm)

    updates = reflector.reflect(_make_episode(), "good night")

    assert updates == []


def test_reflect_llm_none(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    reflector = EpisodeReflector(kv=kv, llm=None)

    updates = reflector.reflect(_make_episode(), "it was too cold")

    assert updates == []


def test_reflect_disabled(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    llm = FakeLLM(
        {"preferences": [{"key": "sleep.preferred_temp_c", "value": 22, "reason": "x"}]}
    )
    reflector = EpisodeReflector(kv=kv, llm=llm, enabled=False)

    updates = reflector.reflect(_make_episode(), "it was too cold")

    assert updates == []


def test_reflect_llm_exception(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    reflector = EpisodeReflector(kv=kv, llm=ExplodingLLM())

    updates = reflector.reflect(_make_episode(), "it was too cold")

    assert updates == []


def test_reflect_skips_none_values(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    llm = FakeLLM(
        {
            "preferences": [
                {"key": "sleep.preferred_temp_c", "value": None, "reason": "null"},
            ]
        }
    )
    reflector = EpisodeReflector(kv=kv, llm=llm)

    updates = reflector.reflect(_make_episode(), "something")

    assert updates == []


def test_reflect_logs_event(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    llm = FakeLLM(
        {
            "preferences": [
                {"key": "comfort.preferred_temp_c", "value": 24, "reason": "comfy"},
            ]
        }
    )
    reflector = EpisodeReflector(kv=kv, llm=llm)

    reflector.reflect(_make_episode("comfort_adjust"), "make it 24")

    # Check that an event was logged
    events = kv.recent_events(limit=5)
    matching = [e for e in events if e.get("type") == "reflection_preferences_applied"]
    assert len(matching) == 1
    assert matching[0]["payload"]["intent"] == "comfort_adjust"


def test_allowed_pref_keys_complete():
    """Ensure all allowed keys follow the expected naming convention."""
    for key in ALLOWED_PREF_KEYS:
        parts = key.split(".")
        assert len(parts) == 2, f"Key {key!r} should have exactly one dot"
        assert parts[0] in {"sleep", "focus", "comfort"}, f"Unknown prefix in {key!r}"
