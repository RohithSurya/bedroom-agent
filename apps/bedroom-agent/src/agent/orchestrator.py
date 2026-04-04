from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

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
    evaluate_sleep_mode,
)
from core.cooldowns import CooldownStore


# ── Action builders ──────────────────────────────────────────────────────────
# Each returns (actions, decision_override).
# decision_override is non-None only when the builder needs to change the
# decision (e.g. comfort_adjust → already_comfortable).

ActionBuildResult = tuple[list[AgentAction], PolicyDecision | None]
ActionBuilder = Callable[[ActionFactory, dict[str, Any], dict[str, Any]], ActionBuildResult]


def _resolve_light_entity_id(args: dict[str, Any], state: dict[str, Any]) -> str:
    return str(
        args.get("light_entity_id") or state.get("light_entity_id") or "light.bedroom_light"
    )


def _build_fan_on(
    factory: ActionFactory, args: dict[str, Any], state: dict[str, Any]
) -> ActionBuildResult:
    entity_id = args.get("entity_id") or state.get("fan_entity_id", "fan.bedroom_fan")
    return (
        [
            factory.fan(entity_id=entity_id, state="on"),
            factory.speech(message="Fan on."),
        ],
        None,
    )


def _build_fan_off(
    factory: ActionFactory, args: dict[str, Any], state: dict[str, Any]
) -> ActionBuildResult:
    entity_id = args.get("entity_id") or state.get("fan_entity_id", "fan.bedroom_fan")
    return (
        [
            factory.fan(entity_id=entity_id, state="off"),
            factory.speech(message="Fan off."),
        ],
        None,
    )


def _build_enter_room(
    factory: ActionFactory, args: dict[str, Any], state: dict[str, Any]
) -> ActionBuildResult:
    entity_id = _resolve_light_entity_id(args, state)
    return ([factory.light(entity_id=entity_id, state="on")], None)


def _build_sleep_mode(
    factory: ActionFactory, args: dict[str, Any], state: dict[str, Any]
) -> ActionBuildResult:
    actions: list[AgentAction] = []

    light_entity_id = _resolve_light_entity_id(args, state)
    if str(state.get("light_state", "")).lower() != "off":
        actions.append(factory.light(entity_id=light_entity_id, state="off"))

    ac_available = bool(state.get("ac_available"))
    sleep_mode_enable_climate = bool(state.get("sleep_mode_enable_climate"))
    comfort_use_fan_fallback = bool(state.get("comfort_use_fan_fallback"))
    temperature_c = state.get("temperature_c")
    sleep_effective_temp_c = int(
        state.get(
            "sleep_preferred_temp_c",
            state.get("sleep_target_temp_c", 24),
        )
    )
    ac_entity_id = str(state.get("ac_entity_id", "climate.bedroom_ac"))
    fan_entity_id = str(state.get("fan_entity_id", "fan.bedroom_fan"))

    should_cool = (
        ac_available
        and sleep_mode_enable_climate
        and isinstance(temperature_c, (int, float))
        and float(temperature_c) > float(sleep_effective_temp_c)
    )

    if should_cool:
        actions.append(
            factory.climate(
                entity_id=ac_entity_id,
                hvac_mode="cool",
                temperature=sleep_effective_temp_c,
                fan_mode="low",
            )
        )
        actions.append(
            factory.speech(
                message=f"Sleep mode on. Turning off the light and cooling to {sleep_effective_temp_c}C."
            )
        )
    elif ac_available and sleep_mode_enable_climate:
        actions.append(
            factory.climate(
                entity_id=ac_entity_id,
                hvac_mode="fan_only",
                temperature=None,
                fan_mode="low",
            )
        )
        actions.append(
            factory.speech(
                message="Sleep mode on. Turning off the light and keeping the room gentle with low fan."
            )
        )
    elif comfort_use_fan_fallback:
        actions.append(factory.fan(entity_id=fan_entity_id, state="on"))
        actions.append(
            factory.speech(
                message="Sleep mode on. Turning off the light and using fan fallback."
            )
        )
    else:
        actions.append(factory.speech(message="Sleep mode on. Turning off the light."))

    return (actions, None)


def _build_focus_start(
    factory: ActionFactory, args: dict[str, Any], state: dict[str, Any]
) -> ActionBuildResult:
    actions: list[AgentAction] = []

    light_entity_id = _resolve_light_entity_id(args, state)
    if str(state.get("light_state", "")).lower() != "on":
        actions.append(factory.light(entity_id=light_entity_id, state="on"))

    if bool(state.get("focus_mode_enable_climate")) and bool(state.get("ac_available")):
        actions.append(
            factory.climate(
                entity_id=str(state.get("ac_entity_id", "climate.bedroom_ac")),
                hvac_mode="fan_only",
                temperature=None,
                fan_mode="low",
            )
        )
    elif bool(state.get("focus_mode_enable_fan")) and bool(
        state.get("comfort_use_fan_fallback")
    ):
        actions.append(
            factory.fan(
                entity_id=str(state.get("fan_entity_id", "fan.bedroom_fan")),
                state="on",
            )
        )
    actions.append(factory.speech(message="Focus mode on."))

    return (actions, None)


def _build_focus_end(
    factory: ActionFactory, args: dict[str, Any], state: dict[str, Any]
) -> ActionBuildResult:
    return ([factory.speech(message="Focus mode off.")], None)


def _build_comfort_adjust(
    factory: ActionFactory, args: dict[str, Any], state: dict[str, Any]
) -> ActionBuildResult:
    if not bool(state.get("room_uncomfortable")):
        # Override: room is already comfortable
        override = PolicyDecision(
            decision="allow",
            reason="already_comfortable",
            cooldown_seconds=0,
            safety_checks=[],
        )
        return ([], override)

    actions: list[AgentAction] = []
    if bool(state.get("ac_available")):
        actions.append(
            factory.climate(
                entity_id=str(state.get("ac_entity_id", "climate.bedroom_ac")),
                hvac_mode="cool",
                temperature=int(state.get("comfort_target_temp_c", 24)),
                fan_mode="auto",
            )
        )
    elif bool(state.get("comfort_use_fan_fallback")):
        actions.append(
            factory.fan(
                entity_id=str(state.get("fan_entity_id", "fan.bedroom_fan")),
                state="on",
            )
        )
    return (actions, None)


def _build_no_action(
    factory: ActionFactory, args: dict[str, Any], state: dict[str, Any]
) -> ActionBuildResult:
    return ([], None)


# ── Intent plan table ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class IntentPlan:
    policy_fn: Callable[[dict[str, Any]], PolicyDecision]
    build_actions: ActionBuilder
    cooldown_key_template: str
    speech_on_deny: str | None = None


def _fan_cooldown_key(args: dict[str, Any], state: dict[str, Any]) -> str:
    entity_id = args.get("entity_id") or state.get("fan_entity_id", "fan.bedroom_fan")
    return f"intent:fan_power:entity:{entity_id}"


def _no_action_policy(state: dict[str, Any]) -> PolicyDecision:
    return PolicyDecision(decision="allow", reason="no_action", cooldown_seconds=0, safety_checks=[])


INTENT_TABLE: dict[str, IntentPlan] = {
    "fan_on": IntentPlan(
        policy_fn=evaluate_fan_power,
        build_actions=_build_fan_on,
        cooldown_key_template="__fan__",  # special: resolved by _fan_cooldown_key
        speech_on_deny="Fan blocked: {reason}",
    ),
    "fan_off": IntentPlan(
        policy_fn=evaluate_fan_power,
        build_actions=_build_fan_off,
        cooldown_key_template="__fan__",
        speech_on_deny="Fan blocked: {reason}",
    ),
    "enter_room": IntentPlan(
        policy_fn=evaluate_enter_room,
        build_actions=_build_enter_room,
        cooldown_key_template="intent:enter_room:room:bedroom",
    ),
    "sleep_mode": IntentPlan(
        policy_fn=evaluate_sleep_mode,
        build_actions=_build_sleep_mode,
        cooldown_key_template="intent:sleep_mode:room:bedroom",
    ),
    "focus_start": IntentPlan(
        policy_fn=evaluate_focus_start,
        build_actions=_build_focus_start,
        cooldown_key_template="intent:focus_start:room:bedroom",
    ),
    "focus_end": IntentPlan(
        policy_fn=evaluate_focus_end,
        build_actions=_build_focus_end,
        cooldown_key_template="intent:focus_end:room:bedroom",
    ),
    "comfort_adjust": IntentPlan(
        policy_fn=evaluate_comfort_adjust,
        build_actions=_build_comfort_adjust,
        cooldown_key_template="intent:comfort_adjust:room:bedroom",
    ),
    "no_action": IntentPlan(
        policy_fn=_no_action_policy,
        build_actions=_build_no_action,
        cooldown_key_template="",
    ),
}


# ── Orchestrator ─────────────────────────────────────────────────────────────


class Orchestrator:
    def __init__(self, cooldowns: CooldownStore | None = None) -> None:
        self.cooldowns = cooldowns or CooldownStore()
        self.action_factory = ActionFactory()

    def handle_request(
        self, *, intent: str, args: dict[str, Any], state: dict[str, Any]
    ) -> dict[str, Any]:
        cid = new_correlation_id()

        plan = INTENT_TABLE.get(intent)
        if plan is None:
            return self._unknown_intent(cid, intent)

        # Policy evaluation
        decision = plan.policy_fn(state)

        # Resolve cooldown key
        if plan.cooldown_key_template == "__fan__":
            cooldown_key = _fan_cooldown_key(args, state)
        else:
            cooldown_key = plan.cooldown_key_template or None

        cooldown_seconds = decision.cooldown_seconds

        # Apply cooldown
        if cooldown_key:
            decision = self._apply_cooldown(cooldown_key=cooldown_key, decision=decision)

        # Build actions or deny speech
        if decision.decision == "allow":
            actions, decision_override = plan.build_actions(
                self.action_factory, args, state
            )
            if decision_override is not None:
                decision = decision_override
                # Override may clear cooldown (e.g. already_comfortable)
                if decision_override.cooldown_seconds == 0:
                    cooldown_key = None
                    cooldown_seconds = 0
        elif plan.speech_on_deny:
            actions = [
                self.action_factory.speech(
                    message=plan.speech_on_deny.format(reason=_humanize_reason(decision.reason))
                )
            ]
        else:
            actions = []

        return {
            "correlation_id": cid,
            "decision": decision,
            "actions": self._materialize_actions(cid, actions),
            "cooldown_key": cooldown_key,
            "cooldown_seconds": cooldown_seconds,
        }

    def _unknown_intent(self, cid: str, intent: str) -> dict[str, Any]:
        decision = PolicyDecision(
            decision="deny",
            reason=f"unknown_intent:{intent}",
            cooldown_seconds=0,
            safety_checks=[],
        )
        actions = [
            self.action_factory.speech(
                message=f"Blocked: {_humanize_reason(decision.reason)}"
            )
        ]
        return {
            "correlation_id": cid,
            "decision": decision,
            "actions": self._materialize_actions(cid, actions),
            "cooldown_key": None,
            "cooldown_seconds": 0,
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
        return "I don't detect anyone in the room."
    if reason == "environment_unavailable":
        return "Temperature and humidity data are unavailable."
    if reason == "no_comfort_path_available":
        return "No cooling path is available right now."
    if reason == "already_comfortable":
        return "The room is already comfortable."
    if reason == "no_action":
        return "No action was needed."
    if reason.startswith("unknown_intent:"):
        return "I don't recognize that request yet."
    return reason
