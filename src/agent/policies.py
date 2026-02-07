from __future__ import annotations

from contracts.policy import PolicyDecision


def evaluate_night_mode(state: dict) -> PolicyDecision:
    """
    v0 policy with:
    - guest_mode gate
    - presence gate
    - cooldown gate (uses simulated clock: state['now_s'])
    """
    now_s = int(state.get("now_s", 0))
    cooldowns: dict = state.get("cooldowns", {})

    until = int(cooldowns.get("night_mode_until", 0))
    if now_s < until:
        remaining = until - now_s
        return PolicyDecision(
            decision="deny",
            reason=f"cooldown_active ({remaining}s remaining)",
            cooldown_seconds=0,
            safety_checks=["cooldown"],
        )

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

    return PolicyDecision(
        decision="allow",
        reason="night_mode requested; presence ok; guest_mode off",
        cooldown_seconds=300,  # 5 min cooldown
        safety_checks=["guest_mode_off", "presence_required", "cooldown"],
    )
