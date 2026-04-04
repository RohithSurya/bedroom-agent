from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from llm.base import LLMClient
from memory.sqlite_kv import SqliteKV


ALLOWED_PREF_KEYS: frozenset[str] = frozenset(
    {
        "sleep.preferred_temp_c",
        "sleep.prefer_lights_off",
        "focus.prefer_fan",
        "focus.prefer_climate",
        "focus.preferred_temp_c",
        "comfort.preferred_temp_c",
        "comfort.prefer_fan",
        "comfort.prefer_climate",
    }
)

REFLECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["preferences"],
    "properties": {
        "preferences": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["key", "value", "reason"],
                "properties": {
                    "key": {"type": "string"},
                    "value": {},
                    "reason": {"type": "string", "maxLength": 80},
                },
            },
            "maxItems": 3,
        },
    },
}


@dataclass
class EpisodeReflector:
    kv: SqliteKV
    llm: Optional[LLMClient]
    enabled: bool = True

    def reflect(self, episode: dict[str, Any], user_text: str) -> list[dict[str, Any]]:
        """Extract and persist preference updates from a completed episode.

        Returns list of applied preference updates (empty if none/error/disabled).
        Safe to call in a background thread — exceptions are caught.
        """
        if not self.enabled or self.llm is None:
            return []

        intent = episode.get("intent", "")
        plan_summary = episode.get("plan_summary", [])

        prompt = (
            "You are a preference extractor for a bedroom automation agent. "
            "Given the completed episode and user message, extract any implicit or explicit "
            "preferences the user expressed that should influence future behavior.\n\n"
            f"Episode intent: {intent}\n"
            f"Actions taken: {plan_summary}\n"
            f"User said: {user_text!r}\n\n"
            f"Allowed preference keys: {', '.join(sorted(ALLOWED_PREF_KEYS))}\n"
            'Return JSON. If no preferences detected, return {"preferences": []}.'
        )

        try:
            out = self.llm.generate_json(
                prompt=prompt, schema=REFLECTION_SCHEMA, temperature=0.0
            )
        except Exception:
            return []

        preferences = out.get("preferences")
        if not isinstance(preferences, list):
            return []

        updates: list[dict[str, Any]] = []
        for pref in preferences:
            if not isinstance(pref, dict):
                continue
            key = str(pref.get("key", ""))
            value = pref.get("value")
            reason = str(pref.get("reason", ""))

            # Only write allowed keys
            if key not in ALLOWED_PREF_KEYS:
                continue
            if value is None:
                continue

            self.kv.set("prefs", key, value)
            updates.append({"key": key, "value": value, "reason": reason})

        if updates:
            self.kv.append_event(
                "reflection_preferences_applied",
                {
                    "intent": intent,
                    "updates": updates,
                    "user_text": user_text,
                },
            )

        return updates
