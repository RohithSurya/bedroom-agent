from __future__ import annotations

from agent.policies import evaluate_night_mode


def test_night_mode_allows_when_present_and_not_guest():
    d = evaluate_night_mode({"presence": True, "guest_mode": False})
    assert d.decision == "allow"


def test_night_mode_denies_when_no_presence():
    d = evaluate_night_mode({"presence": False, "guest_mode": False})
    assert d.decision == "deny"
