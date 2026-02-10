from __future__ import annotations

from typing import Any

from contracts.ha import ToolCall
from contracts.policy import PolicyDecision
from core.ids import new_correlation_id, new_idempotency_key
from agent.policies import evaluate_night_mode
from core.cooldowns import CooldownStore


class Orchestrator:
    def __init__(self, cooldowns: CooldownStore) -> None:
        self.cooldowns = cooldowns

    def handle_request(
        self, *, intent: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        cid = new_correlation_id()

        if intent == "night_mode":
            cooldown_key = f"intent:{intent}:room:bedroom"
            decision = evaluate_night_mode(state)
            cooldown_seconds = decision.cooldown_seconds
            actions: list[ToolCall] = []

            if decision.decision == "allow" and decision.cooldown_seconds > 0:
                allowed, remaining = self.cooldowns.can_run(cooldown_key, decision.cooldown_seconds)
                if not allowed:
                    decision = PolicyDecision(
                        decision="deny",
                        reason=f"cooldown_active:{remaining}s_remaining",
                        cooldown_key=cooldown_key,
                        cooldown_seconds=cooldown_seconds,
                        safety_checks=[],
                    )

            if decision.decision == "allow":
                # v0 lights-only defaults
                entity_id = args.get("entity_id", "light.bedroom_lamp")
                brightness_pct = int(args.get("brightness_pct", 15))
                transition_s = float(args.get("transition_s", 2))

                actions.append(
                    ToolCall(
                        tool="light.set",
                        args={
                            "entity_id": entity_id,
                            "brightness_pct": brightness_pct,
                            "transition_s": transition_s,
                        },
                        idempotency_key=new_idempotency_key(),
                        correlation_id=cid,
                    )
                )
                actions.append(
                    ToolCall(
                        tool="tts.say",
                        args={"message": "Night mode on. Lights dimmed."},
                        idempotency_key=new_idempotency_key(),
                        correlation_id=cid,
                    )
                )
            else:
                actions.append(
                    ToolCall(
                        tool="tts.say",
                        args={
                            "message": f"Night mode blocked: {_humanize_reason(decision.reason)}"
                        },
                        idempotency_key=new_idempotency_key(),
                        correlation_id=cid,
                    )
                )

            return {
                "correlation_id": cid,
                "decision": decision,
                "actions": actions,
                "cooldown_seconds": cooldown_seconds,
                "cooldown_key": cooldown_key,
            }

        decision = PolicyDecision(decision="deny", reason=f"unknown_intent:{intent}")
        return {
            "correlation_id": cid,
            "decision": decision,
            "cooldown_key": None,
            "cooldown_seconds": 0,
            "actions": [
                ToolCall(
                    tool="tts.say",
                    args={"message": "Sorry, I don't recognize that request yet."},
                    idempotency_key=new_idempotency_key(),
                    correlation_id=cid,
                )
            ],
        }


def _humanize_reason(reason: str) -> str:
    if reason.startswith("cooldown_active:"):
        # cooldown_active:59s_remaining
        try:
            part = reason.split(":", 1)[1]
            secs = part.split("s_", 1)[0]
            return f"Cooldown active. Try again in {secs} seconds."
        except Exception:
            return "Cooldown active. Try again soon."
    if reason == "guest_mode_on":
        return "Guest Mode is on."
    if reason == "presence_required":
        return "I don’t detect anyone in the room."
    if reason.startswith("unknown_intent:"):
        return "I don’t recognize that request yet."
    if reason == "ok":
        return "OK"
    return reason
