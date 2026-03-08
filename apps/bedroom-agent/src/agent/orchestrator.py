from __future__ import annotations

from typing import Any

from agent.actions import ActionFactory, AgentAction
from contracts.ha import ToolCall
from contracts.policy import PolicyDecision
from core.ids import new_correlation_id
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
        self.action_factory = ActionFactory()

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
            actions: list[AgentAction] = []

            decision = self._apply_cooldown(
                cooldown_key=cooldown_key,
                decision=decision,
                include_safety_check=False,
            )

            if decision.decision == "allow":
                entity_id = self._resolve_light_entity_id(args=args, state=state)
                actions.append(
                    self.action_factory.light(entity_id=entity_id, state="off")
                )
                actions.append(
                    self.action_factory.speech(message="Night mode on. Lights dimmed.")
                )
            else:
                actions.append(
                    self.action_factory.speech(
                        message=f"Night mode blocked: {_humanize_reason(decision.reason)}"
                    )
                )

            return {
                "correlation_id": cid,
                "decision": decision,
                "actions": self._materialize_actions(cid, actions),
                "cooldown_seconds": cooldown_seconds,
                "cooldown_key": cooldown_key,
            }

        # ---------- fan_on / fan_off ----------
        if intent in ("fan_on", "fan_off"):
            decision = evaluate_fan_power(state)
            actions: list[AgentAction] = []

            entity_id = args.get("entity_id") or state.get("fan_entity_id", "fan.bedroom_fan")
            desired = "on" if intent == "fan_on" else "off"

            cooldown_key = f"intent:fan_power:entity:{entity_id}"
            cooldown_seconds = decision.cooldown_seconds

            decision = self._apply_cooldown(cooldown_key=cooldown_key, decision=decision)

            if decision.decision == "allow":
                actions.append(self.action_factory.fan(entity_id=entity_id, state=desired))
                actions.append(
                    self.action_factory.speech(message=f"Fan {desired}.")
                )
            else:
                actions.append(
                    self.action_factory.speech(
                        message=f"Fan blocked: {_humanize_reason(decision.reason)}"
                    )
                )

            return {
                "correlation_id": cid,
                "decision": decision,
                "actions": self._materialize_actions(cid, actions),
                "cooldown_key": cooldown_key,
                "cooldown_seconds": cooldown_seconds,
            }

        # ---------- enter_room ----------
        if intent == "enter_room":
            decision = evaluate_enter_room(state)
            actions: list[AgentAction] = []

            entity_id = self._resolve_light_entity_id(args=args, state=state)

            cooldown_key = "intent:enter_room:room:bedroom"
            cooldown_seconds = decision.cooldown_seconds

            decision = self._apply_cooldown(cooldown_key=cooldown_key, decision=decision)

            if decision.decision == "allow":
                actions.append(self.action_factory.light(entity_id=entity_id, state="on"))

            return {
                "correlation_id": cid,
                "decision": decision,
                "actions": self._materialize_actions(cid, actions),
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
        actions = [self.action_factory.speech(message=f"Blocked: {_humanize_reason(decision.reason)}")]
        return {
            "correlation_id": cid,
            "decision": decision,
            "actions": self._materialize_actions(cid, actions),
            "cooldown_key": None,
            "cooldown_seconds": 0,
        }

    def _handle_sleep_mode(
        self, *, cid: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        decision = evaluate_sleep_mode(state)
        cooldown_key = "intent:sleep_mode:room:bedroom"
        cooldown_seconds = decision.cooldown_seconds
        actions: list[AgentAction] = []

        decision = self._apply_cooldown(
            cooldown_key=cooldown_key,
            decision=decision,
        )
        if decision.decision == "allow":
            light_entity_id = self._resolve_light_entity_id(args=args, state=state)
            if str(state.get("light_state", "")).lower() != "off":
                actions.append(self.action_factory.light(entity_id=light_entity_id, state="off"))

            if (
                bool(state.get("sleep_mode_enable_climate"))
                and bool(state.get("room_uncomfortable"))
                and bool(state.get("ac_available"))
            ):
                actions.append(
                    self.action_factory.climate(
                        entity_id=str(state.get("ac_entity_id", "climate.bedroom_ac")),
                        hvac_mode="cool",
                        temperature=int(state.get("sleep_target_temp_c", 24)),
                        fan_mode="low",
                    )
                )
            actions.append(self.action_factory.speech(message="Sleep mode on."))
        return {
            "correlation_id": cid,
            "decision": decision,
            "actions": self._materialize_actions(cid, actions),
            "cooldown_key": cooldown_key,
            "cooldown_seconds": cooldown_seconds,
        }

    def _handle_focus_start(
        self, *, cid: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        decision = evaluate_focus_start(state)
        cooldown_key = "intent:focus_start:room:bedroom"
        cooldown_seconds = decision.cooldown_seconds
        actions: list[AgentAction] = []

        decision = self._apply_cooldown(
            cooldown_key=cooldown_key,
            decision=decision,
        )
        if decision.decision == "allow":
            light_entity_id = self._resolve_light_entity_id(args=args, state=state)
            if str(state.get("light_state", "")).lower() != "on":
                actions.append(self.action_factory.light(entity_id=light_entity_id, state="on"))

            if bool(state.get("room_uncomfortable")):
                if bool(state.get("focus_mode_enable_climate")) and bool(state.get("ac_available")):
                    actions.append(
                        self.action_factory.climate(
                            entity_id=str(state.get("ac_entity_id", "climate.bedroom_ac")),
                            hvac_mode="cool",
                            temperature=int(state.get("comfort_target_temp_c", 24)),
                            fan_mode="auto",
                        )
                    )
                elif bool(state.get("focus_mode_enable_fan")) and bool(
                    state.get("comfort_use_fan_fallback")
                ):
                    actions.append(
                        self.action_factory.fan(
                            entity_id=str(state.get("fan_entity_id", "fan.bedroom_fan")),
                            state="on",
                        )
                    )
            actions.append(self.action_factory.speech(message="Focus mode on."))
        return {
            "correlation_id": cid,
            "decision": decision,
            "actions": self._materialize_actions(cid, actions),
            "cooldown_key": cooldown_key,
            "cooldown_seconds": cooldown_seconds,
        }

    def _handle_focus_end(
        self, *, cid: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        decision = evaluate_focus_end(state)
        cooldown_key = "intent:focus_end:room:bedroom"
        cooldown_seconds = decision.cooldown_seconds
        actions: list[AgentAction] = []

        decision = self._apply_cooldown(
            cooldown_key=cooldown_key,
            decision=decision,
        )
        if decision.decision == "allow":
            actions.append(self.action_factory.speech(message="Focus mode off."))
        return {
            "correlation_id": cid,
            "decision": decision,
            "actions": self._materialize_actions(cid, actions),
            "cooldown_key": cooldown_key,
            "cooldown_seconds": cooldown_seconds,
        }

    def _handle_comfort_adjust(
        self, *, cid: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        decision = evaluate_comfort_adjust(state)
        cooldown_key = "intent:comfort_adjust:room:bedroom"
        cooldown_seconds = decision.cooldown_seconds
        actions: list[AgentAction] = []

        decision = self._apply_cooldown(
            cooldown_key=cooldown_key,
            decision=decision,
        )

        if decision.decision == "allow":
            if bool(state.get("room_uncomfortable")):
                if bool(state.get("ac_available")):
                    actions.append(
                        self.action_factory.climate(
                            entity_id=str(state.get("ac_entity_id", "climate.bedroom_ac")),
                            hvac_mode="cool",
                            temperature=int(state.get("comfort_target_temp_c", 24)),
                            fan_mode="auto",
                        )
                    )
                elif bool(state.get("comfort_use_fan_fallback")):
                    actions.append(
                        self.action_factory.fan(
                            entity_id=str(state.get("fan_entity_id", "fan.bedroom_fan")),
                            state="on",
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
            "actions": self._materialize_actions(cid, actions),
            "cooldown_key": cooldown_key,
            "cooldown_seconds": cooldown_seconds,
        }

    def _apply_cooldown(
        self,
        *,
        cooldown_key: str,
        decision: PolicyDecision,
        include_safety_check: bool = True,
    ) -> PolicyDecision:
        cooldown_seconds = decision.cooldown_seconds
        if decision.decision != "allow" or cooldown_seconds <= 0:
            return decision
        allowed, remaining = self.cooldowns.can_run(cooldown_key, cooldown_seconds)
        if allowed:
            return decision
        safety_checks = list(decision.safety_checks)
        if include_safety_check:
            safety_checks.append("cooldown")
        return PolicyDecision(
            decision="deny",
            reason=f"cooldown_active:{remaining}s_remaining",
            cooldown_seconds=cooldown_seconds,
            safety_checks=safety_checks,
        )

    def _resolve_light_entity_id(self, *, args: dict[str, Any], state: dict[str, Any]) -> str:
        return str(args.get("light_entity_id") or state.get("light_entity_id") or "light.bedroom_light")

    def _materialize_actions(
        self, correlation_id: str, actions: list[AgentAction]
    ) -> list[ToolCall]:
        tool_calls: list[ToolCall] = []
        for action in actions:
            tool_calls.extend(action.to_tool_calls(correlation_id))
        return tool_calls


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
