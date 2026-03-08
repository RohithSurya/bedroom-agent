from __future__ import annotations

from contracts.policy import PolicyDecision


def _deny(reason: str, *checks: str) -> PolicyDecision:
    return PolicyDecision(
        decision="deny",
        reason=reason,
        cooldown_seconds=0,
        safety_checks=list(checks),
    )


def evaluate_fan_power(state: dict) -> PolicyDecision:
    if state.get("guest_mode", False):
        return _deny("guest_mode_on", "guest_mode_off")

    if not state.get("presence", False):
        return _deny("presence_required", "presence_required")

    return PolicyDecision(
        decision="allow",
        reason="ok",
        cooldown_seconds=5,  # fan toggle cooldown (tweak as you like)
        safety_checks=["guest_mode_off", "presence_required"],
    )


def evaluate_enter_room(state: dict) -> PolicyDecision:
    if state.get("guest_mode", False):
        return _deny("guest_mode_on", "guest_mode_off")

    # require presence; with Option B, presence is your belief state
    if not state.get("presence", False):
        return _deny("presence_required", "presence_required")

    return PolicyDecision(
        decision="allow",
        reason="ok",
        cooldown_seconds=60,  # prevent spam if sensors bounce
        safety_checks=["guest_mode_off", "presence_required"],
    )


def evaluate_sleep_mode(state: dict) -> PolicyDecision:
    if state.get("guest_mode", False):
        return _deny("guest_mode_on", "guest_mode_off")
    if not state.get("presence", False):
        return _deny("presence_required", "presence_required")
    return PolicyDecision(
        decision="allow",
        reason="ok",
        cooldown_seconds=120,
        safety_checks=["guest_mode_off", "presence_required"],
    )


def evaluate_focus_start(state: dict) -> PolicyDecision:
    if state.get("guest_mode", False):
        return _deny("guest_mode_on", "guest_mode_off")
    if not state.get("presence", False):
        return _deny("presence_required", "presence_required")
    return PolicyDecision(
        decision="allow",
        reason="ok",
        cooldown_seconds=60,
        safety_checks=["guest_mode_off", "presence_required"],
    )


def evaluate_focus_end(state: dict) -> PolicyDecision:
    return PolicyDecision(
        decision="allow",
        reason="ok",
        cooldown_seconds=15,
        safety_checks=[],
    )


def evaluate_comfort_adjust(state: dict) -> PolicyDecision:
    if state.get("guest_mode", False):
        return _deny("guest_mode_on", "guest_mode_off")
    if not state.get("presence", False):
        return _deny("presence_required", "presence_required")
    if state.get("temperature_c") is None and state.get("humidity_pct") is None:
        return _deny("environment_unavailable", "environment_required")
    if not state.get("ac_available", False) and not state.get("comfort_use_fan_fallback", False):
        return _deny("no_comfort_path_available", "climate_or_fan_required")
    return PolicyDecision(
        decision="allow",
        reason="ok",
        cooldown_seconds=180,
        safety_checks=["guest_mode_off", "presence_required", "environment_required"],
    )
