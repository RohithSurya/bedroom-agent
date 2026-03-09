from memory.preference_feedback import PreferenceFeedback
from memory.sqlite_kv import SqliteKV


def test_preference_feedback_warmer_updates_sleep_temp(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    kv.set("prefs", "sleep.preferred_temp_c", 24)

    feedback = PreferenceFeedback(kv=kv)
    result = feedback.apply(
        user_text="That was too cold. A bit warmer next time.",
        last_episode={"intent": "sleep_mode"},
    )

    assert result is not None
    assert result["updates"]["sleep.preferred_temp_c"] == 25
    assert kv.get("prefs", "sleep.preferred_temp_c") == 25


def test_preference_feedback_cooler_updates_sleep_temp(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    kv.set("prefs", "sleep.preferred_temp_c", 24)

    feedback = PreferenceFeedback(kv=kv)
    result = feedback.apply(
        user_text="Too warm. Make it cooler next time.",
        last_episode={"intent": "sleep_mode"},
    )

    assert result is not None
    assert result["updates"]["sleep.preferred_temp_c"] == 23
    assert kv.get("prefs", "sleep.preferred_temp_c") == 23


def test_preference_feedback_lights_off_updates_pref(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    kv.set("prefs", "sleep.prefer_lights_off", False)

    feedback = PreferenceFeedback(kv=kv)
    result = feedback.apply(
        user_text="Keep the light off next time.",
        last_episode={"intent": "sleep_mode"},
    )

    assert result is not None
    assert result["updates"]["sleep.prefer_lights_off"] is True
    assert kv.get("prefs", "sleep.prefer_lights_off") is True


def test_preference_feedback_ignores_when_no_matching_episode(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    feedback = PreferenceFeedback(kv=kv)

    result = feedback.apply(
        user_text="Too cold",
        last_episode={"intent": "focus_start"},
    )

    assert result is None
