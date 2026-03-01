from __future__ import annotations

from typing import Any

from contracts.ha import ToolCall
from contracts.policy import PolicyDecision
from core.ids import new_correlation_id, new_idempotency_key
from agent.policies import evaluate_night_mode, evaluate_fan_power, evaluate_enter_room
from core.cooldowns import CooldownStore


class Orchestrator:
    def __init__(self, cooldowns: CooldownStore | None = None) -> None:
        self.cooldowns = cooldowns or CooldownStore()

    def handle_request(
        self, *, intent: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        cid = new_correlation_id()

        # ---------- night_mode ----------
        if intent == "night_mode":
            cooldown_key = f"intent:{intent}:room:bedroom"
            decision = evaluate_night_mode(state)
            cooldown_seconds = decision.cooldown_seconds
            actions: list[ToolCall] = []

            if decision.decision == "allow" and cooldown_seconds > 0:
                allowed, remaining = self.cooldowns.can_run(cooldown_key, cooldown_seconds)
                if not allowed:
                    decision = PolicyDecision(
                        decision="deny",
                        reason=f"cooldown_active:{remaining}s_remaining",
                        cooldown_seconds=cooldown_seconds,
                        safety_checks=[],
                    )

            if decision.decision == "allow":
                entity_id = args.get("entity_id", "switch.bedroom_light_switch")

                actions.append(
                    ToolCall(
                        tool="switch.set",
                        args={
                            "entity_id": entity_id,
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

        # ---------- fan_on / fan_off ----------
        if intent in ("fan_on", "fan_off"):
            decision = evaluate_fan_power(state)
            actions: list[ToolCall] = []

            entity_id = args.get("entity_id", "switch.bedroom_fan_plug")
            desired = "on" if intent == "fan_on" else "off"

            cooldown_key = f"intent:fan_power:entity:{entity_id}"
            cooldown_seconds = decision.cooldown_seconds

            if decision.decision == "allow" and cooldown_seconds > 0:
                ok, remaining = self.cooldowns.can_run(cooldown_key, cooldown_seconds)
                if not ok:
                    decision = PolicyDecision(
                        decision="deny",
                        reason=f"cooldown_active:{remaining}s_remaining",
                        cooldown_seconds=cooldown_seconds,
                        safety_checks=decision.safety_checks + ["cooldown"],
                    )

            if decision.decision == "allow":
                actions.append(
                    ToolCall(
                        tool="switch.set",
                        args={"entity_id": entity_id, "state": desired},
                        idempotency_key=new_idempotency_key(),
                        correlation_id=cid,
                    )
                )
                actions.append(
                    ToolCall(
                        tool="tts.say",
                        args={"message": f"Fan {desired}."},
                        idempotency_key=new_idempotency_key(),
                        correlation_id=cid,
                    )
                )
            else:
                actions.append(
                    ToolCall(
                        tool="tts.say",
                        args={"message": f"Fan blocked: {_humanize_reason(decision.reason)}"},
                        idempotency_key=new_idempotency_key(),
                        correlation_id=cid,
                    )
                )

            return {
                "correlation_id": cid,
                "decision": decision,
                "actions": actions,
                "cooldown_key": cooldown_key,
                "cooldown_seconds": cooldown_seconds,
            }

        # ---------- enter_room ----------
        if intent == "enter_room":
            decision = evaluate_enter_room(state)
            actions: list[ToolCall] = []

            entity_id = args.get("entity_id", "switch.bedroom_light_switch")

            cooldown_key = "intent:enter_room:room:bedroom"
            cooldown_seconds = decision.cooldown_seconds

            if decision.decision == "allow" and cooldown_seconds > 0:
                ok, remaining = self.cooldowns.can_run(cooldown_key, cooldown_seconds)
                if not ok:
                    decision = PolicyDecision(
                        decision="deny",
                        reason=f"cooldown_active:{remaining}s_remaining",
                        cooldown_seconds=cooldown_seconds,
                        safety_checks=decision.safety_checks + ["cooldown"],
                    )

            if decision.decision == "allow":
                entity_id = args.get("entity_id", "switch.bedroom_light_switch")

                actions.append(
                    ToolCall(
                        tool="switch.set",
                        args={"entity_id": entity_id, "state": "on"},
                        idempotency_key=new_idempotency_key(),
                        correlation_id=cid,
                    )
                )

            return {
                "correlation_id": cid,
                "decision": decision,
                "actions": actions,
                "cooldown_key": cooldown_key,
                "cooldown_seconds": cooldown_seconds,
            }

        # ---------- unknown intent (safe) ----------
        decision = PolicyDecision(
            decision="deny", reason=f"unknown_intent:{intent}", cooldown_seconds=0, safety_checks=[]
        )
        actions = [
            ToolCall(
                tool="tts.say",
                args={"message": f"Blocked: {_humanize_reason(decision.reason)}"},
                idempotency_key=new_idempotency_key(),
                correlation_id=cid,
            )
        ]
        return {
            "correlation_id": cid,
            "decision": decision,
            "actions": actions,
            "cooldown_key": None,
            "cooldown_seconds": 0,
        }


def _humanize_reason(reason: str) -> str:
    if reason.startswith("cooldown_active:"):
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
    return reason
