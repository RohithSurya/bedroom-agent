from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from memory.sqlite_kv import SqliteKV


@dataclass
class PreferenceFeedback:
    kv: SqliteKV

    def apply(
        self, *, user_text: str, last_episode: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        text = (user_text or "").strip().lower()
        if not text:
            return None

        if not isinstance(last_episode, dict):
            return None

        last_intent = str(last_episode.get("intent", "") or "")
        if last_intent != "sleep_mode":
            return None

        updates: dict[str, Any] = {}
        message: str | None = None

        current_temp = self._get_int_pref("sleep.preferred_temp_c", default=24)

        warmer_phrases = (
            "too cold",
            "colder than i wanted",
            "a bit warmer",
            "warmer next time",
            "make it warmer",
        )
        cooler_phrases = (
            "too warm",
            "hotter than i wanted",
            "a bit cooler",
            "cooler next time",
            "make it cooler",
        )
        lights_off_phrases = (
            "keep the light off",
            "lights off next time",
            "no light next time",
            "turn the light off next time",
        )

        if any(p in text for p in warmer_phrases):
            new_temp = min(current_temp + 1, 30)
            self.kv.set("prefs", "sleep.preferred_temp_c", new_temp)
            updates["sleep.preferred_temp_c"] = new_temp
            message = f"Got it. I’ll aim slightly warmer for sleep next time at {new_temp}C."

        elif any(p in text for p in cooler_phrases):
            new_temp = max(current_temp - 1, 16)
            self.kv.set("prefs", "sleep.preferred_temp_c", new_temp)
            updates["sleep.preferred_temp_c"] = new_temp
            message = f"Got it. I’ll aim slightly cooler for sleep next time at {new_temp}C."

        elif any(p in text for p in lights_off_phrases):
            self.kv.set("prefs", "sleep.prefer_lights_off", True)
            updates["sleep.prefer_lights_off"] = True
            message = "Understood. I’ll prefer keeping the lights off for sleep next time."

        elif "light" in text and ("on" in text or "leave it on" in text):
            self.kv.set("prefs", "sleep.prefer_lights_off", False)
            updates["sleep.prefer_lights_off"] = False
            message = "Understood. I’ll avoid forcing lights fully off for sleep next time."

        if not updates:
            return None

        self.kv.append_event(
            "preference_feedback_applied",
            {
                "last_intent": last_intent,
                "updates": updates,
                "user_text": user_text,
            },
        )

        return {
            "applied": True,
            "intent_scope": last_intent,
            "updates": updates,
            "message": message,
        }

    def _get_int_pref(self, key: str, *, default: int) -> int:
        raw = self.kv.get("prefs", key, default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
