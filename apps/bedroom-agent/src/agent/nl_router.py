from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from agent.intent_registry import ROUTER_INTENTS
from llm.base import LLMClient


AllowedIntent = Literal[
    "fan_on",
    "fan_off",
    "sleep_mode",
    "focus_start",
    "focus_end",
    "comfort_adjust",
    "analyze_bedroom",
    "status",
    "decision_request",
]


ROUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "args"],
    "properties": {
        "intent": {
            "type": "string",
            "enum": list(ROUTER_INTENTS),
        },
        "args": {"type": "object"},
    },
}


@dataclass
class NLRouter:
    """Maps natural language to your existing orchestrator intents.

    Important design choice:
    - The LLM chooses ONLY a *high-level intent + args*.
    - The Orchestrator + PolicyGate remain deterministic and safety-critical.
    """

    llm: Optional[LLMClient] = None

    def route(self, *, text: str, state: dict[str, Any]) -> tuple[AllowedIntent, dict[str, Any]]:
        t = (text or "").strip().lower()

        # Fast deterministic shortcuts (keeps latency low and reduces LLM calls)
        if "fan" in t and ("on" in t or "start" in t):
            return "fan_on", {}
        if "fan" in t and ("off" in t or "stop" in t):
            return "fan_off", {}
        if ("end" in t or "stop" in t or "off" in t) and ("focus" in t or "deep work" in t):
            return "focus_end", {}
        if any(
            phrase in t
            for phrase in (
                "make the room ready for sleep",
                "start sleep mode",
                "sleep mode",
                "help me wind down",
                "wind down",
                "bedtime",
            )
        ):
            return "sleep_mode", {}
        if any(
            phrase in t
            for phrase in (
                "set the room up for focus",
                "start focus mode",
                "help me focus",
                "focus mode",
            )
        ):
            return "focus_start", {}
        if any(
            phrase in t
            for phrase in (
                "make the room comfortable",
                "cool the room",
                "cool room",
                "adjust comfort",
            )
        ):
            return "comfort_adjust", {}
        if "what should happen now" in t:
            return "decision_request", {}
        if any(
            phrase in t
            for phrase in (
                "analyze",
                "check bedroom",
                "analyze bedroom",
                "analyze my room",
                "is this room good for focus",
                "what should i fix before sleep",
                "room good for focus",
                "before sleep",
            )
        ):
            return "analyze_bedroom", {}
        if self._looks_like_visual_query(t):
            return "analyze_bedroom", {}
        if any(
            k in t
            for k in [
                "status",
                "how am i",
                "summary",
                "how long",
                "why did",
                "what happened",
                "recent events",
            ]
        ):
            return "status", {"query": text}

        # LLM routing (optional). If unavailable, fall back to status.
        if self.llm is None:
            return "status", {"query": text}

        prompt = (
            "You are a router for a bedroom automation agent. "
            "Choose the single best intent from the allowed list and return JSON only.\n\n"
            "Allowed intents:\n"
            "- fan_on / fan_off\n"
            "- sleep_mode: prepare the room for sleep or bedtime\n"
            "- focus_start / focus_end: start or end focus mode\n"
            "- comfort_adjust: cool the room or make it more comfortable\n"
            "- analyze_bedroom: run camera analysis for bed/desk/floor\n"
            "- decision_request: user is asking an ambiguous high-level question like what should happen now\n"
            "- status: answer questions about current state / sessions\n\n"
            f"User text: {text!r}\n"
            'Return: {"intent": ..., "args": {...}}\n'
        )

        out = self.llm.generate_json(prompt=prompt, schema=ROUTER_SCHEMA, temperature=0.0)
        intent = out.get("intent")
        args = out.get("args") if isinstance(out.get("args"), dict) else {}

        if intent in ROUTER_SCHEMA["properties"]["intent"]["enum"]:
            return intent, args

        return "status", {"query": text}

    def _looks_like_visual_query(self, text: str) -> bool:
        question_markers = (
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
        scene_markers = (
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
        return any(marker in text for marker in question_markers) and any(
            marker in text for marker in scene_markers
        )
