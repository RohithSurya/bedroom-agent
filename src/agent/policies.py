from __future__ import annotations

from contracts.policy import PolicyDecision


def evaluate_night_mode(state: dict) -> PolicyDecision:
    # v0: lights-only
    if state.get("guest_mode", False):
        return PolicyDecision(
            decision="deny",
            reason="guest_mode is on",
            cooldown_seconds=0,
            safety_checks=["guest_mode_off"],
        )

    if not state.get("presence", False):
        return PolicyDecision(
            decision="deny",
            reason="no presence detected",
            cooldown_seconds=0,
            safety_checks=["presence_required"],
        )

    # Lights-only cooldown (prevents repeated triggers)
    return PolicyDecision(
        decision="allow",
        reason="night_mode requested; presence ok; guest_mode off",
        cooldown_seconds=60,
        safety_checks=["guest_mode_off", "presence_required"],
    )


def evaluate_fan_power(state: dict) -> PolicyDecision:
    if state.get("guest_mode", False):
        return PolicyDecision(
            decision="deny",
            reason="guest_mode_on",
            cooldown_seconds=0,
            safety_checks=["guest_mode_off"],
        )

    if not state.get("presence", False):
        return PolicyDecision(
            decision="deny",
            reason="presence_required",
            cooldown_seconds=0,
            safety_checks=["presence_required"],
        )

    return PolicyDecision(
        decision="allow",
        reason="ok",
        cooldown_seconds=5,  # fan toggle cooldown (tweak as you like)
        safety_checks=["guest_mode_off", "presence_required"],
    )
