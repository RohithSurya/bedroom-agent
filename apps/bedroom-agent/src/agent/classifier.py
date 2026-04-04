from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from agent.intent_registry import CLASSIFIER_INTENTS, CLASSIFIER_SCHEMA
from llm.base import LLMClient
from memory.sqlite_kv import SqliteKV


@dataclass(frozen=True)
class Classification:
    intent: str
    args: dict[str, Any]
    confidence: float
    is_feedback: bool
    source: str  # "deterministic" | "llm" | "fallback"


# ── Canonical phrase lists (single source of truth) ──────────────────────────

_FEEDBACK_PHRASES: tuple[str, ...] = (
    # temperature
    "too cold",
    "colder than i wanted",
    "a bit warmer",
    "warmer next time",
    "make it warmer",
    "too warm",
    "hotter than i wanted",
    "a bit cooler",
    "cooler next time",
    "make it cooler",
    # lights
    "keep the light off",
    "lights off next time",
    "no light next time",
    "turn the light off next time",
)

_SLEEP_PHRASES: tuple[str, ...] = (
    "make the room ready for sleep",
    "start sleep mode",
    "sleep mode",
    "help me wind down",
    "wind down",
    "bedtime",
)

_FOCUS_START_PHRASES: tuple[str, ...] = (
    "set the room up for focus",
    "start focus mode",
    "help me focus",
    "focus mode",
)

_COMFORT_PHRASES: tuple[str, ...] = (
    "make the room comfortable",
    "cool the room",
    "cool room",
    "adjust comfort",
)

_ANALYZE_PHRASES: tuple[str, ...] = (
    "analyze",
    "check bedroom",
    "analyze bedroom",
    "analyze my room",
    "is this room good for focus",
    "what should i fix before sleep",
    "room good for focus",
    "before sleep",
)

_STATUS_KEYWORDS: tuple[str, ...] = (
    "status",
    "how am i",
    "summary",
    "how long",
    "why did",
    "what happened",
    "recent events",
)

_QUESTION_MARKERS: tuple[str, ...] = (
    "do you see",
    "can you see",
    "is there",
    "are there",
    "what is on",
    "what's on",
    "what do you see",
    "does the",
    "is the",
    "where is",
    "how many",
)

_SCENE_MARKERS: tuple[str, ...] = (
    "bed",
    "bedroom",
    "chair",
    "closet",
    "desk",
    "dresser",
    "floor",
    "lamp",
    "laundry",
    "light",
    "mirror",
    "monitor",
    "pillow",
    "screen",
    "table",
)


@dataclass
class Classifier:
    """Unified text-to-intent classifier.

    Replaces NLRouter + DecisionEngine fast-paths + PreferenceFeedback detection.
    The LLM is used only when deterministic shortcuts don't match.
    """

    llm: Optional[LLMClient] = None
    kv: Optional[SqliteKV] = None

    def classify(
        self,
        *,
        text: str,
        state: dict[str, Any] | None = None,
        session_turns: list[Any] | None = None,
    ) -> Classification:
        t = (text or "").strip().lower()

        # # 1. Feedback detection
        # feedback = self._detect_feedback(t)
        # if feedback is not None:
        #     return feedback

        # # 2. Deterministic fast-path
        # fast = self._fast_path(t, text)
        # if fast is not None:
        #     return fast

        # 3. LLM fallback
        if self.llm is not None:
            llm_result = self._llm_classify(text, session_turns)
            if llm_result is not None:
                return llm_result

        # 4. Default
        return Classification(
            intent="status",
            args={"query": text},
            confidence=0.3,
            is_feedback=False,
            source="fallback",
        )

    def _detect_feedback(self, text: str) -> Classification | None:
        """Check if this looks like preference feedback about a recent episode."""
        if self.kv is None:
            return None

        # Check for feedback phrases
        has_feedback_phrase = any(p in text for p in _FEEDBACK_PHRASES)
        # Also catch "light on" / "leave it on" pattern
        has_light_on = "light" in text and ("on" in text or "leave it on" in text)

        if not has_feedback_phrase and not has_light_on:
            return None

        # Verify there's a relevant last episode
        last = self.kv.get("episodes", "last", None)
        if not isinstance(last, dict):
            return None

        last_intent = str(last.get("intent", "") or "")
        if not last_intent:
            return None

        return Classification(
            intent="preference_feedback",
            args={"text": text, "last_intent": last_intent},
            confidence=0.95,
            is_feedback=True,
            source="deterministic",
        )

    def _fast_path(self, t: str, original_text: str) -> Classification | None:
        # Fan control
        if "fan" in t and ("on" in t or "start" in t):
            return self._det("fan_on", {})
        if "fan" in t and ("off" in t or "stop" in t):
            return self._det("fan_off", {})

        # Focus end (before focus start — "stop focus" should match end, not start)
        if ("end" in t or "stop" in t or "off" in t) and ("focus" in t or "deep work" in t):
            return self._det("focus_end", {})

        # Sleep mode
        if any(phrase in t for phrase in _SLEEP_PHRASES):
            return self._det("sleep_mode", {})

        # Focus start
        if any(phrase in t for phrase in _FOCUS_START_PHRASES):
            return self._det("focus_start", {})

        # Comfort adjust
        if any(phrase in t for phrase in _COMFORT_PHRASES):
            return self._det("comfort_adjust", {})

        # Analyze bedroom
        if any(phrase in t for phrase in _ANALYZE_PHRASES):
            return self._det("analyze_bedroom", {})
        if self._looks_like_visual_query(t):
            return self._det("analyze_bedroom", {})

        # Status
        if any(k in t for k in _STATUS_KEYWORDS):
            return self._det("status", {"query": original_text})

        return None

    def _looks_like_visual_query(self, text: str) -> bool:
        return any(m in text for m in _QUESTION_MARKERS) and any(m in text for m in _SCENE_MARKERS)

    def _llm_classify(
        self, text: str, session_turns: list[Any] | None = None
    ) -> Classification | None:
        prompt = (
            "You are a router for a bedroom automation agent. "
            "Choose the single best intent from the allowed list and return JSON only.\n\n"
            "State your initial logic, verify it once, and then output the result immediately.\n"
            "Provide a concise reasoning process (max 3 steps) before the final answer.\n"
            "Allowed intents:\n"
            "- fan_on / fan_off\n"
            "- sleep_mode: prepare the room for sleep or bedtime\n"
            "- focus_start / focus_end: start or end focus mode\n"
            "- comfort_adjust: cool the room or make it more comfortable\n"
            "- analyze_bedroom: run camera analysis for bed/desk/floor\n"
            "- preference_feedback: user is giving feedback about a previous action\n"
            "- status: answer questions about current state / sessions\n"
            "- no_action: nothing useful to do\n\n"
        )

        if session_turns:
            history_lines = []
            for turn in session_turns[-6:]:
                role = getattr(turn, "role", "user")
                turn_text = getattr(turn, "text", str(turn))
                history_lines.append(f"  {role}: {turn_text}")
            if history_lines:
                prompt += "Recent conversation:\n" + "\n".join(history_lines) + "\n\n"

        prompt += f'User text: {text!r}\nReturn: {{"intent": ..., "args": {{...}}}}\n'
        print(prompt)

        try:
            out = self.llm.generate_json(prompt=prompt, schema=CLASSIFIER_SCHEMA, temperature=0.0)
        except Exception:
            return None
        print(out)
        intent = out.get("intent")
        args = out.get("args") if isinstance(out.get("args"), dict) else {}

        if intent in CLASSIFIER_INTENTS:
            return Classification(
                intent=intent,
                args=args,
                confidence=0.7,
                is_feedback=(intent == "preference_feedback"),
                source="llm",
            )

        return None

    @staticmethod
    def _det(intent: str, args: dict[str, Any]) -> Classification:
        return Classification(
            intent=intent,
            args=args,
            confidence=0.99,
            is_feedback=False,
            source="deterministic",
        )
