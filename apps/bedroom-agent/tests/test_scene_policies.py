from __future__ import annotations

from agent.policies import (
    evaluate_comfort_adjust,
    evaluate_focus_start,
    evaluate_sleep_mode,
)


def test_sleep_mode_denies_without_presence():
    decision = evaluate_sleep_mode({"presence": False, "guest_mode": False})
    assert decision.decision == "deny"
    assert decision.reason == "presence_required"


def test_focus_start_allows_when_present_and_not_guest():
    decision = evaluate_focus_start({"presence": True, "guest_mode": False})
    assert decision.decision == "allow"


def test_comfort_adjust_requires_environment_and_path():
    decision = evaluate_comfort_adjust(
        {
            "presence": True,
            "guest_mode": False,
            "temperature_c": None,
            "humidity_pct": None,
            "ac_available": False,
            "comfort_use_fan_fallback": False,
        }
    )
    assert decision.decision == "deny"


def test_comfort_adjust_allows_with_presence_and_ac():
    decision = evaluate_comfort_adjust(
        {
            "presence": True,
            "guest_mode": False,
            "temperature_c": 27.0,
            "humidity_pct": 68.0,
            "ac_available": True,
            "comfort_use_fan_fallback": False,
        }
    )
    assert decision.decision == "allow"
