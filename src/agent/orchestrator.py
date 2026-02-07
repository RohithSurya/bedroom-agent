from __future__ import annotations

from typing import Any

from contracts.ha import ToolCall
from contracts.policy import PolicyDecision
from core.ids import new_correlation_id, new_idempotency_key
from agent.policies import evaluate_night_mode


class Orchestrator:
    def __init__(self) -> None:
        pass

    def handle_request(
        self, *, intent: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        cid = new_correlation_id()

        if intent == "night_mode":
            decision = evaluate_night_mode(state)
            actions: list[ToolCall] = []

            if decision.decision == "allow":
                actions.append(
                    ToolCall(
                        tool="light.set_scene",
                        args={"scene": "night_dim"},
                        idempotency_key=new_idempotency_key(),
                        correlation_id=cid,
                    )
                )
                actions.append(
                    ToolCall(
                        tool="tts.say",
                        args={"message": "Night mode on."},
                        idempotency_key=new_idempotency_key(),
                        correlation_id=cid,
                    )
                )
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

        # default: unknown intent
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
