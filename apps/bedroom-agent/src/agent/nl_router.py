from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional

from llm.ollama_client import OllamaClient


AllowedIntent = Literal[
    "night_mode",
    "fan_on",
    "fan_off",
    "analyze_bedroom",
    "focus_start",
    "focus_end",
    "status",
]


ROUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "args"],
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "night_mode",
                "fan_on",
                "fan_off",
                "analyze_bedroom",
                "focus_start",
                "focus_end",
                "status",
            ],
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

    llm: Optional[OllamaClient] = None

    def route(self, *, text: str, state: dict[str, Any]) -> tuple[AllowedIntent, dict[str, Any]]:
        t = (text or "").strip().lower()

        # Fast deterministic shortcuts (keeps latency low and reduces LLM calls)
        if "night mode" in t or (t.startswith("night") and "mode" in t):
            return "night_mode", {}
        if "fan" in t and ("on" in t or "start" in t):
            return "fan_on", {}
        if "fan" in t and ("off" in t or "stop" in t):
            return "fan_off", {}
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
        if "start" in t and ("focus" in t or "deep work" in t):
            return "focus_start", {}
        if "end" in t and ("focus" in t or "deep work" in t):
            return "focus_end", {}
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
            "- night_mode: dim lights + announce\n"
            "- fan_on / fan_off\n"
            "- analyze_bedroom: run camera analysis for bed/desk/floor\n"
            "- focus_start / focus_end\n"
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
