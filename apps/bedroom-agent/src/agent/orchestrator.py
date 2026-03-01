from __future__ import annotations

from typing import Any

from contracts.ha import ToolCall
from contracts.policy import PolicyDecision
from core.ids import new_correlation_id, new_idempotency_key
from agent.policies import (
    evaluate_comfort_adjust,
    evaluate_enter_room,
    evaluate_fan_power,
    evaluate_focus_end,
    evaluate_focus_start,
    evaluate_night_mode,
    evaluate_sleep_mode,
)
from core.cooldowns import CooldownStore


class Orchestrator:
    def __init__(self, cooldowns: CooldownStore | None = None) -> None:
        self.cooldowns = cooldowns or CooldownStore()

    def handle_request(
        self, *, intent: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        cid = new_correlation_id()

        if intent == "no_action":
            decision = PolicyDecision(
                decision="allow", reason="no_action", cooldown_seconds=0, safety_checks=[]
            )
            return {
                "correlation_id": cid,
                "decision": decision,
                "actions": [],
                "cooldown_key": None,
                "cooldown_seconds": 0,
            }

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
                entity_id = args.get("entity_id") or state.get("light_entity_id", "switch.bedroom_light_switch")

                actions.append(
                    self._light_call(cid, entity_id=entity_id, state="off")
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

            entity_id = args.get("entity_id") or state.get("fan_entity_id", "switch.bedroom_fan_plug")
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

            entity_id = args.get("entity_id") or state.get("light_entity_id", "switch.bedroom_light_switch")

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
                actions.append(self._light_call(cid, entity_id=entity_id, state="on"))

            return {
                "correlation_id": cid,
                "decision": decision,
                "actions": actions,
                "cooldown_key": cooldown_key,
                "cooldown_seconds": cooldown_seconds,
            }

        if intent == "sleep_mode":
            return self._handle_sleep_mode(cid=cid, args=args, state=state)

        if intent == "focus_start":
            return self._handle_focus_start(cid=cid, args=args, state=state)

        if intent == "focus_end":
            return self._handle_focus_end(cid=cid, args=args, state=state)

        if intent == "comfort_adjust":
            return self._handle_comfort_adjust(cid=cid, args=args, state=state)

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

    def _handle_sleep_mode(
        self, *, cid: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        decision = evaluate_sleep_mode(state)
        cooldown_key = "intent:sleep_mode:room:bedroom"
        cooldown_seconds = decision.cooldown_seconds
        actions: list[ToolCall] = []

        decision = self._apply_cooldown(
            cooldown_key=cooldown_key,
            cooldown_seconds=cooldown_seconds,
            decision=decision,
        )
        if decision.decision == "allow":
            light_entity_id = args.get("light_entity_id") or state.get(
                "light_entity_id", "switch.bedroom_light_switch"
            )
            if str(state.get("light_state", "")).lower() != "off":
                actions.append(self._light_call(cid, entity_id=light_entity_id, state="off"))

            if bool(state.get("sleep_mode_enable_climate")) and bool(state.get("room_uncomfortable")) and bool(
                state.get("ac_available")
            ):
                actions.extend(
                    self._cooling_actions(
                        cid,
                        entity_id=str(state.get("ac_entity_id", "climate.bedroom_ac")),
                        temperature=int(state.get("sleep_target_temp_c", 24)),
                        fan_mode="low",
                    )
                )
            actions.append(
                ToolCall(
                    tool="tts.say",
                    args={"message": "Sleep mode on."},
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

    def _handle_focus_start(
        self, *, cid: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        decision = evaluate_focus_start(state)
        cooldown_key = "intent:focus_start:room:bedroom"
        cooldown_seconds = decision.cooldown_seconds
        actions: list[ToolCall] = []

        decision = self._apply_cooldown(
            cooldown_key=cooldown_key,
            cooldown_seconds=cooldown_seconds,
            decision=decision,
        )
        if decision.decision == "allow":
            light_entity_id = args.get("light_entity_id") or state.get(
                "light_entity_id", "switch.bedroom_light_switch"
            )
            if str(state.get("light_state", "")).lower() != "on":
                actions.append(self._light_call(cid, entity_id=light_entity_id, state="on"))

            if bool(state.get("room_uncomfortable")):
                if bool(state.get("focus_mode_enable_climate")) and bool(state.get("ac_available")):
                    actions.extend(
                        self._cooling_actions(
                            cid,
                            entity_id=str(state.get("ac_entity_id", "climate.bedroom_ac")),
                            temperature=int(state.get("comfort_target_temp_c", 24)),
                            fan_mode="auto",
                        )
                    )
                elif bool(state.get("focus_mode_enable_fan")) and bool(state.get("comfort_use_fan_fallback")):
                    actions.append(
                        ToolCall(
                            tool="switch.set",
                            args={
                                "entity_id": str(state.get("fan_entity_id", "switch.bedroom_fan_plug")),
                                "state": "on",
                            },
                            idempotency_key=new_idempotency_key(),
                            correlation_id=cid,
                        )
                    )
            actions.append(
                ToolCall(
                    tool="tts.say",
                    args={"message": "Focus mode on."},
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

    def _handle_focus_end(
        self, *, cid: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        decision = evaluate_focus_end(state)
        cooldown_key = "intent:focus_end:room:bedroom"
        cooldown_seconds = decision.cooldown_seconds
        actions: list[ToolCall] = []

        decision = self._apply_cooldown(
            cooldown_key=cooldown_key,
            cooldown_seconds=cooldown_seconds,
            decision=decision,
        )
        if decision.decision == "allow":
            actions.append(
                ToolCall(
                    tool="tts.say",
                    args={"message": "Focus mode off."},
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

    def _handle_comfort_adjust(
        self, *, cid: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        decision = evaluate_comfort_adjust(state)
        cooldown_key = "intent:comfort_adjust:room:bedroom"
        cooldown_seconds = decision.cooldown_seconds
        actions: list[ToolCall] = []

        decision = self._apply_cooldown(
            cooldown_key=cooldown_key,
            cooldown_seconds=cooldown_seconds,
            decision=decision,
        )

        if decision.decision == "allow":
            if bool(state.get("room_uncomfortable")):
                if bool(state.get("ac_available")):
                    actions.extend(
                        self._cooling_actions(
                            cid,
                            entity_id=str(state.get("ac_entity_id", "climate.bedroom_ac")),
                            temperature=int(state.get("comfort_target_temp_c", 24)),
                            fan_mode="auto",
                        )
                    )
                elif bool(state.get("comfort_use_fan_fallback")):
                    actions.append(
                        ToolCall(
                            tool="switch.set",
                            args={
                                "entity_id": str(state.get("fan_entity_id", "switch.bedroom_fan_plug")),
                                "state": "on",
                            },
                            idempotency_key=new_idempotency_key(),
                            correlation_id=cid,
                        )
                    )
            else:
                decision = PolicyDecision(
                    decision="allow",
                    reason="already_comfortable",
                    cooldown_seconds=0,
                    safety_checks=decision.safety_checks,
                )
                cooldown_key = None
                cooldown_seconds = 0
        return {
            "correlation_id": cid,
            "decision": decision,
            "actions": actions,
            "cooldown_key": cooldown_key,
            "cooldown_seconds": cooldown_seconds,
        }

    def _apply_cooldown(
        self,
        *,
        cooldown_key: str,
        cooldown_seconds: int,
        decision: PolicyDecision,
    ) -> PolicyDecision:
        if decision.decision != "allow" or cooldown_seconds <= 0:
            return decision
        allowed, remaining = self.cooldowns.can_run(cooldown_key, cooldown_seconds)
        if allowed:
            return decision
        return PolicyDecision(
            decision="deny",
            reason=f"cooldown_active:{remaining}s_remaining",
            cooldown_seconds=cooldown_seconds,
            safety_checks=decision.safety_checks + ["cooldown"],
        )

    def _light_call(self, correlation_id: str, *, entity_id: str, state: str) -> ToolCall:
        tool = "light.set" if entity_id.startswith("light.") else "switch.set"
        return ToolCall(
            tool=tool,
            args={"entity_id": entity_id, "state": state},
            idempotency_key=new_idempotency_key(),
            correlation_id=correlation_id,
        )

    def _cooling_actions(
        self, correlation_id: str, *, entity_id: str, temperature: int, fan_mode: str
    ) -> list[ToolCall]:
        return [
            ToolCall(
                tool="climate.set_mode",
                args={"entity_id": entity_id, "hvac_mode": "cool"},
                idempotency_key=new_idempotency_key(),
                correlation_id=correlation_id,
            ),
            ToolCall(
                tool="climate.set_temperature",
                args={"entity_id": entity_id, "temperature": int(temperature)},
                idempotency_key=new_idempotency_key(),
                correlation_id=correlation_id,
            ),
            ToolCall(
                tool="climate.set_fan_mode",
                args={"entity_id": entity_id, "fan_mode": fan_mode},
                idempotency_key=new_idempotency_key(),
                correlation_id=correlation_id,
            ),
        ]


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
    if reason == "environment_unavailable":
        return "Temperature and humidity data are unavailable."
    if reason == "no_comfort_path_available":
        return "No cooling path is available right now."
    if reason == "already_comfortable":
        return "The room is already comfortable."
    if reason == "no_action":
        return "No action was needed."
    if reason.startswith("unknown_intent:"):
        return "I don’t recognize that request yet."
    return reason
