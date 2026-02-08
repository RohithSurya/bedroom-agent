from __future__ import annotations

from typing import Any

from contracts.ha import ToolCall
from contracts.policy import PolicyDecision
from core.ids import new_correlation_id, new_idempotency_key
from agent.policies import evaluate_night_mode
from core.cooldowns import CooldownStore


class Orchestrator:
    def __init__(self) -> None:
        self.cooldowns = CooldownStore()

    def handle_request(
        self, *, intent: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        cid = new_correlation_id()

        if intent == "night_mode":
            cooldown_key = f"intent:{intent}:room:bedroom"
            decision = evaluate_night_mode(state)
            actions: list[ToolCall] = []

            if decision.decision == "allow" and decision.cooldown_seconds > 0:
                allowed, remaining = self.cooldowns.can_run(cooldown_key, decision.cooldown_seconds)
                if not allowed:
                    decision = PolicyDecision(
                        decision="deny",
                        reason=f"cooldown active: {remaining} seconds remaining",
                        cooldown_seconds=0,
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
                self.cooldowns.mark_ran(cooldown_key, decision.cooldown_seconds)
            else:
                actions.append(
                    ToolCall(
                        tool="tts.say",
                        args={"message": f"Night mode blocked: {decision.reason}"},
                        idempotency_key=new_idempotency_key(),
                        correlation_id=cid,
                    )
                )

            return {
                "correlation_id": cid,
                "decision": decision,
                "actions": actions,
            }

        decision = PolicyDecision(decision="deny", reason=f"unknown_intent:{intent}")
        return {
            "correlation_id": cid,
            "decision": decision,
            "actions": [
                ToolCall(
                    tool="tts.say",
                    args={"message": "Sorry, I don't recognize that request yet."},
                    idempotency_key=new_idempotency_key(),
                    correlation_id=cid,
                )
            ],
        }
