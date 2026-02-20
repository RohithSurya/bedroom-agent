from __future__ import annotations

from typing import Any

from contracts.ha import ToolCall
from contracts.policy import PolicyDecision
from core.ids import new_correlation_id, new_idempotency_key
from agent.policies import evaluate_night_mode
from agent.policies import evaluate_fan_power
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
        if intent in ("fan_on", "fan_off"):
            decision = evaluate_fan_power(state)
            actions: list[ToolCall] = []

            entity_id = args.get("entity_id", "switch.bedroom_fan_plug")
            desired = "on" if intent == "fan_on" else "off"

            # Cooldown key per-device + per-intent family
            cooldown_key = f"intent:fan_power:entity:{entity_id}"
            cooldown_seconds = decision.cooldown_seconds

            # cooldown check (read-only here)
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
                        args={"message": "cool down"},
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

        actions.append(
            ToolCall(
                tool="tts.say",
                args={"message": f"Fan request blocked: {_humanize_reason(decision.reason)}"},
                idempotency_key=new_idempotency_key(),
                correlation_id=cid,
            )
        )
        return {
            "correlation_id": cid,
            "decision": decision,
            "actions": actions,
            "cooldown_key": None,
            "cooldown_seconds": 0,
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
