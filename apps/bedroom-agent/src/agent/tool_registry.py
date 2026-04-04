from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolDefinition:
    """Single source of truth for tool metadata.

    Used by: ToolBehaviorRegistry (verification/retry), LLM context (description/parameters).
    """

    name: str
    description: str
    parameters: dict[str, Any]
    entity_type: str  # "light", "fan", "climate", "tts", "switch"
    requires_verification: bool
    retryable: bool


TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "light.set": ToolDefinition(
        name="light.set",
        description="Turn a light on or off, optionally set brightness",
        parameters={
            "entity_id": {"type": "string"},
            "state": {"type": "string", "enum": ["on", "off"]},
            "brightness_pct": {"type": "integer", "optional": True},
            "transition_s": {"type": "number", "optional": True},
        },
        entity_type="light",
        requires_verification=True,
        retryable=True,
    ),
    "fan.set": ToolDefinition(
        name="fan.set",
        description="Turn a fan on or off",
        parameters={
            "entity_id": {"type": "string"},
            "state": {"type": "string", "enum": ["on", "off"]},
        },
        entity_type="fan",
        requires_verification=False,
        retryable=True,
    ),
    "switch.set": ToolDefinition(
        name="switch.set",
        description="Turn a switch on or off",
        parameters={
            "entity_id": {"type": "string"},
            "state": {"type": "string", "enum": ["on", "off"]},
        },
        entity_type="switch",
        requires_verification=False,
        retryable=True,
    ),
    "climate.set_mode": ToolDefinition(
        name="climate.set_mode",
        description="Set HVAC mode (off, cool, fan_only, auto)",
        parameters={
            "entity_id": {"type": "string"},
            "hvac_mode": {"type": "string", "enum": ["off", "cool", "fan_only", "auto"]},
        },
        entity_type="climate",
        requires_verification=False,
        retryable=True,
    ),
    "climate.set_temperature": ToolDefinition(
        name="climate.set_temperature",
        description="Set target temperature for climate control",
        parameters={
            "entity_id": {"type": "string"},
            "temperature": {"type": "integer"},
        },
        entity_type="climate",
        requires_verification=False,
        retryable=True,
    ),
    "climate.set_fan_mode": ToolDefinition(
        name="climate.set_fan_mode",
        description="Set fan speed for climate control",
        parameters={
            "entity_id": {"type": "string"},
            "fan_mode": {"type": "string", "enum": ["auto", "low", "medium", "high"]},
        },
        entity_type="climate",
        requires_verification=False,
        retryable=True,
    ),
    "tts.say": ToolDefinition(
        name="tts.say",
        description="Speak a message via text-to-speech",
        parameters={
            "message": {"type": "string"},
        },
        entity_type="tts",
        requires_verification=False,
        retryable=False,
    ),
}


def get_tool(name: str) -> ToolDefinition:
    """Get a tool definition by name. Raises KeyError if not found."""
    return TOOL_DEFINITIONS[name]


def all_tools() -> list[ToolDefinition]:
    """Return all registered tool definitions."""
    return list(TOOL_DEFINITIONS.values())
