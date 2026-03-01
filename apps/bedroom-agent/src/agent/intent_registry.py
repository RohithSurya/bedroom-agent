from __future__ import annotations

from typing import Any


DECISION_INTENTS: tuple[str, ...] = (
    "sleep_mode",
    "focus_start",
    "focus_end",
    "comfort_adjust",
    "fan_on",
    "fan_off",
    "night_mode",
    "no_action",
)

ROUTER_INTENTS: tuple[str, ...] = (
    "night_mode",
    "fan_on",
    "fan_off",
    "sleep_mode",
    "focus_start",
    "focus_end",
    "comfort_adjust",
    "analyze_bedroom",
    "status",
    "decision_request",
)

DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["intent", "args", "confidence", "rationale", "reasoning_tags"],
    "properties": {
        "intent": {"type": "string", "enum": list(DECISION_INTENTS)},
        "args": {"type": "object"},
        "confidence": {"type": "number"},
        "rationale": {"type": "string", "maxLength": 180},
        "reasoning_tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
}


def is_decision_intent(intent: str) -> bool:
    return intent in DECISION_INTENTS


def is_router_intent(intent: str) -> bool:
    return intent in ROUTER_INTENTS
