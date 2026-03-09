from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Optional

from agent.intent_registry import DECISION_INTENTS, DECISION_SCHEMA
from llm.base import LLMClient
from memory.sqlite_kv import SqliteKV


IMPORTANT_EVENT_TYPES = {
    "enter_detected",
    "door_update",
    "vacancy_detected",
    "vacancy_off_executed",
    "bedroom_analysis_completed",
    "llm_decision_returned",
    "llm_intent_executed",
}


@dataclass
class DecisionChoice:
    intent: str
    args: dict[str, Any]
    confidence: float
    rationale: str
    reasoning_tags: list[str]
    fallback_used: bool
    source: str
    trigger: str
    trace: dict[str, Any]

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionEngine:
    kv: SqliteKV
    llm: Optional[LLMClient]
    max_events: int = 8
    min_confidence: float = 0.55
    use_vision: bool = True

    def choose_intent(
        self,
        *,
        source: str,
        trigger: str,
        user_text: str | None,
        state: dict[str, Any],
    ) -> DecisionChoice:
        fast_path = self._fast_path_choice(source=source, trigger=trigger, user_text=user_text)
        if fast_path is not None:
            return self._finalize_choice(fast_path, state=state, user_text=user_text)

        context = self._build_context(
            source=source,
            trigger=trigger,
            user_text=user_text,
            state=state,
        )
        fallback = self._fallback_choice(
            source=source,
            trigger=trigger,
            user_text=user_text,
        )

        if self.llm is None:
            return self._finalize_choice(fallback, state=state, user_text=user_text)

        prompt = (
            "You are the decision layer for a bedroom automation agent. "
            "Choose exactly one high-level intent from the allowed list. "
            "Never emit raw Home Assistant calls, entity IDs, or free-form action sequences. "
            "Choose no_action when the context does not justify a useful change.\n\n"
            f"Decision context JSON: {json.dumps(context, ensure_ascii=False, sort_keys=True)}\n"
            "Return JSON only."
        )

        try:
            out = self.llm.generate_json(prompt=prompt, schema=DECISION_SCHEMA, temperature=0.0)
        except Exception:
            return self._finalize_choice(fallback, state=state, user_text=user_text)

        intent = str(out.get("intent", "")).strip()
        args = out.get("args") if isinstance(out.get("args"), dict) else {}
        rationale = str(out.get("rationale", "")).strip()
        reasoning_tags = out.get("reasoning_tags")
        confidence = out.get("confidence")

        if intent not in DECISION_INTENTS:
            return self._finalize_choice(fallback, state=state, user_text=user_text)
        if not rationale:
            return self._finalize_choice(fallback, state=state, user_text=user_text)
        if not isinstance(reasoning_tags, list) or not all(
            isinstance(tag, str) and tag.strip() for tag in reasoning_tags
        ):
            return self._finalize_choice(fallback, state=state, user_text=user_text)
        if not isinstance(confidence, (int, float)):
            return self._finalize_choice(fallback, state=state, user_text=user_text)
        if float(confidence) < self.min_confidence:
            return self._finalize_choice(fallback, state=state, user_text=user_text)

        choice = DecisionChoice(
            intent=intent,
            args=args,
            confidence=float(confidence),
            rationale=rationale,
            reasoning_tags=reasoning_tags[:5],
            fallback_used=False,
            source=source,
            trigger=trigger,
            trace={},
        )
        return self._finalize_choice(choice, state=state, user_text=user_text)

    def _fast_path_choice(
        self,
        *,
        source: str,
        trigger: str,
        user_text: str | None,
    ) -> DecisionChoice | None:
        text = (user_text or "").strip().lower()
        if not text:
            return None

        if any(
            phrase in text
            for phrase in (
                "end focus mode",
                "stop focus mode",
                "turn off focus mode",
                "focus mode off",
                "stop deep work",
            )
        ):
            return DecisionChoice(
                intent="focus_end",
                args={},
                confidence=0.99,
                rationale="Deterministic shortcut selected focus_end from the explicit request.",
                reasoning_tags=["deterministic", "focus_end_request"],
                fallback_used=False,
                source=source,
                trigger=trigger,
                trace={},
            )

        if any(
            phrase in text
            for phrase in (
                "make the room ready for sleep",
                "start sleep mode",
                "sleep mode",
                "help me wind down",
                "wind down",
                "bedtime",
            )
        ):
            return DecisionChoice(
                intent="sleep_mode",
                args={},
                confidence=0.99,
                rationale="Deterministic shortcut selected sleep_mode from the explicit request.",
                reasoning_tags=["deterministic", "sleep_request"],
                fallback_used=False,
                source=source,
                trigger=trigger,
                trace={},
            )

        if any(
            phrase in text
            for phrase in (
                "set the room up for focus",
                "start focus mode",
                "help me focus",
                "focus mode",
            )
        ):
            return DecisionChoice(
                intent="focus_start",
                args={},
                confidence=0.99,
                rationale="Deterministic shortcut selected focus_start from the explicit request.",
                reasoning_tags=["deterministic", "focus_request"],
                fallback_used=False,
                source=source,
                trigger=trigger,
                trace={},
            )

        if self._is_comfort_request(user_text):
            return DecisionChoice(
                intent="comfort_adjust",
                args={},
                confidence=0.99,
                rationale="Deterministic shortcut selected comfort_adjust from the explicit request.",
                reasoning_tags=["deterministic", "comfort_request"],
                fallback_used=False,
                source=source,
                trigger=trigger,
                trace={},
            )

        return None

    def _episode_context(self, state: dict[str, Any]) -> dict[str, Any]:
        recent = state.get("recent_episodes", [])
        if not isinstance(recent, list):
            recent = []

        compact_recent: list[dict[str, Any]] = []
        for ep in recent[:3]:
            if not isinstance(ep, dict):
                continue
            compact_recent.append(
                {
                    "intent": ep.get("intent"),
                    "policy_decision": ep.get("policy_decision"),
                    "execution_success": ep.get("execution_success"),
                    "memory_hits": ep.get("memory_hits", []),
                    "plan_summary": ep.get("plan_summary", []),
                }
            )

        return {
            "summary": str(state.get("episode_summary", "") or ""),
            "recent": compact_recent,
        }

    def _build_context(
        self,
        *,
        source: str,
        trigger: str,
        user_text: str | None,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        recent = []
        for event in self.kv.recent_events(limit=max(self.max_events * 3, 12)):
            if event["type"] in IMPORTANT_EVENT_TYPES:
                recent.append(
                    {"type": event["type"], "ts": event["ts"], "payload": event["payload"]}
                )
            elif event["type"] == "presence_update":
                payload = event["payload"]
                if "presence" in payload:
                    recent.append(
                        {
                            "type": event["type"],
                            "ts": event["ts"],
                            "payload": {"presence": payload["presence"]},
                        }
                    )
            if len(recent) >= self.max_events:
                break

        relevant_prefs = state.get("relevant_prefs", {})
        if not isinstance(relevant_prefs, dict):
            relevant_prefs = {}

        context = {
            "source": source,
            "trigger": trigger,
            "user_text": user_text,
            "beliefs": {
                "presence": bool(state.get("presence", False)),
                "door_open": bool(state.get("door_open", False)),
                "guest_mode": bool(state.get("guest_mode", False)),
            },
            "recent_events": recent,
            "environment": {
                "temperature_entity_id": state.get("temperature_entity_id"),
                "temperature_c": state.get("temperature_c"),
                "humidity_entity_id": state.get("humidity_entity_id"),
                "humidity_pct": state.get("humidity_pct"),
                "comfort_trigger_temp_c": state.get("comfort_trigger_temp_c"),
                "comfort_trigger_humidity_pct": state.get("comfort_trigger_humidity_pct"),
                "room_uncomfortable": bool(state.get("room_uncomfortable", False)),
            },
            "device_states": {
                "ac_entity_id": state.get("ac_entity_id"),
                "ac_available": bool(state.get("ac_available", False)),
                "ac_state": state.get("ac_state"),
                "ac_hvac_mode": state.get("ac_hvac_mode"),
                "ac_target_temp_c": state.get("ac_target_temp_c"),
                "ac_fan_mode": state.get("ac_fan_mode"),
                "light_state": state.get("light_state"),
                "fan_state": state.get("fan_state"),
            },
            "memory": {
                "relevant_preferences": relevant_prefs,
                "episodes": self._episode_context(state),
            },
            "allowed_intents": list(DECISION_INTENTS),
        }
        if self.use_vision and isinstance(state.get("vision"), dict):
            context["vision"] = state["vision"]
        return context

    def _bounded_signals(self, state: dict[str, Any]) -> list[str]:
        signals: list[str] = []

        if "presence" in state:
            signals.append(f"presence={bool(state.get('presence', False))}")
        if "guest_mode" in state:
            signals.append(f"guest_mode={bool(state.get('guest_mode', False))}")

        temperature_c = state.get("temperature_c")
        if isinstance(temperature_c, (int, float)):
            signals.append(f"temperature_c={float(temperature_c):.1f}")

        humidity_pct = state.get("humidity_pct")
        if isinstance(humidity_pct, (int, float)):
            signals.append(f"humidity_pct={float(humidity_pct):.1f}")

        if "room_uncomfortable" in state:
            signals.append(f"room_uncomfortable={bool(state.get('room_uncomfortable', False))}")

        if "ac_available" in state:
            signals.append(f"ac_available={bool(state.get('ac_available', False))}")

        vision = state.get("vision")
        if isinstance(vision, dict) and vision.get("available"):
            if "sleep_readiness" in vision:
                signals.append(f"vision.sleep_readiness={vision.get('sleep_readiness')}")
            if "focus_readiness" in vision:
                signals.append(f"vision.focus_readiness={vision.get('focus_readiness')}")
            if "bed_state" in vision:
                signals.append(f"vision.bed_state={vision.get('bed_state')}")
            if "desk_state" in vision:
                signals.append(f"vision.desk_state={vision.get('desk_state')}")

        return signals[:8]

    def _guardrails(self, state: dict[str, Any]) -> list[str]:
        guards: list[str] = []

        guards.append("guest_mode_on" if bool(state.get("guest_mode", False)) else "guest_mode_off")
        guards.append("presence_true" if bool(state.get("presence", False)) else "presence_false")
        guards.append(
            "room_uncomfortable"
            if bool(state.get("room_uncomfortable", False))
            else "room_comfortable"
        )
        guards.append(
            "ac_available" if bool(state.get("ac_available", False)) else "ac_unavailable"
        )

        vision = state.get("vision")
        if isinstance(vision, dict):
            guards.append(
                "vision_available" if bool(vision.get("available")) else "vision_unavailable"
            )

        return guards

    def _build_trace(
        self,
        *,
        choice: DecisionChoice,
        state: dict[str, Any],
        user_text: str | None,
    ) -> dict[str, Any]:
        relevant_prefs = state.get("relevant_prefs", {})
        if not isinstance(relevant_prefs, dict):
            relevant_prefs = {}

        return {
            "goal": (user_text or "").strip()[:120] or choice.trigger,
            "selected_intent": choice.intent,
            "selected_because": choice.rationale,
            "reasoning_tags": choice.reasoning_tags[:5],
            "memory_hits": list(relevant_prefs.keys())[:5],
            "episode_summary": str(state.get("episode_summary", "") or ""),
            "signals": self._bounded_signals(state),
            "guardrails": self._guardrails(state),
            "fallback_used": bool(choice.fallback_used),
        }

    def _finalize_choice(
        self,
        choice: DecisionChoice,
        *,
        state: dict[str, Any],
        user_text: str | None,
    ) -> DecisionChoice:
        aligned = self._align_choice_with_state(choice, state=state, user_text=user_text)
        aligned.trace = self._build_trace(choice=aligned, state=state, user_text=user_text)
        return aligned

    def _fallback_choice(
        self,
        *,
        source: str,
        trigger: str,
        user_text: str | None,
    ) -> DecisionChoice:
        text = (user_text or "").strip().lower()
        intent = "no_action"
        tags = ["fallback"]
        rationale = "No strong deterministic fallback intent matched the request."

        if any(phrase in text for phrase in ("sleep", "wind down", "bedtime")):
            intent = "sleep_mode"
            tags = ["fallback", "sleep_request"]
            rationale = "Fallback selected sleep_mode from the user request."
        elif "focus" in text or "study" in text or "deep work" in text:
            if "end" in text or "stop" in text:
                intent = "focus_end"
                tags = ["fallback", "focus_end_request"]
                rationale = "Fallback selected focus_end from the user request."
            else:
                intent = "focus_start"
                tags = ["fallback", "focus_request"]
                rationale = "Fallback selected focus_start from the user request."
        elif any(
            phrase in text
            for phrase in ("comfortable", "comfort", "cool the room", "cool room", "cool")
        ):
            intent = "comfort_adjust"
            tags = ["fallback", "comfort_request"]
            rationale = "Fallback selected comfort_adjust from the user request."

        return DecisionChoice(
            intent=intent,
            args={},
            confidence=0.51 if intent != "no_action" else 0.4,
            rationale=rationale,
            reasoning_tags=tags,
            fallback_used=True,
            source=source,
            trigger=trigger,
            trace={},
        )

    def _align_choice_with_state(
        self, choice: DecisionChoice, *, state: dict[str, Any], user_text: str | None
    ) -> DecisionChoice:
        if choice.intent == "comfort_adjust" and not bool(state.get("room_uncomfortable", False)):
            return DecisionChoice(
                intent="no_action",
                args={},
                confidence=max(choice.confidence, 0.95),
                rationale=self._comfort_no_action_rationale(state),
                reasoning_tags=["room_comfortable", "thresholds", "no_energy_needed"],
                fallback_used=False,
                source=choice.source,
                trigger=choice.trigger,
                trace={},
            )
        if (
            choice.intent == "no_action"
            and not bool(state.get("room_uncomfortable", False))
            and self._is_comfort_request(user_text)
        ):
            return DecisionChoice(
                intent="no_action",
                args={},
                confidence=max(choice.confidence, 0.9),
                rationale=self._comfort_no_action_rationale(state),
                reasoning_tags=["room_comfortable", "thresholds", "no_energy_needed"],
                fallback_used=choice.fallback_used,
                source=choice.source,
                trigger=choice.trigger,
                trace={},
            )
        return choice

    def _is_comfort_request(self, user_text: str | None) -> bool:
        text = (user_text or "").strip().lower()
        return any(
            phrase in text
            for phrase in ("comfortable", "comfort", "cool the room", "cool room", "cool")
        )

    def _comfort_no_action_rationale(self, state: dict[str, Any]) -> str:
        temperature = state.get("temperature_c")
        humidity = state.get("humidity_pct")
        temp_threshold = state.get("comfort_trigger_temp_c")
        humidity_threshold = state.get("comfort_trigger_humidity_pct")
        temp_part = (
            f"Current temperature ({float(temperature):.2f}°C) is below the cooling threshold ({float(temp_threshold):.0f}°C)."
            if isinstance(temperature, (int, float)) and isinstance(temp_threshold, (int, float))
            else "Current temperature does not justify cooling."
        )
        humidity_part = (
            f" Humidity ({float(humidity):.2f}%) is below the comfort threshold ({float(humidity_threshold):.0f}%)."
            if isinstance(humidity, (int, float)) and isinstance(humidity_threshold, (int, float))
            else ""
        )
        return f"{temp_part}{humidity_part} No cooling action is needed."
