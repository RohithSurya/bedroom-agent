from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from memory.sqlite_kv import SqliteKV


DEFAULT_PREF_KEYS_BY_INTENT: dict[str, list[str]] = {
    "sleep_mode": [
        "sleep.preferred_temp_c",
        "sleep.prefer_lights_off",
    ],
    "focus_start": [
        "focus.prefer_fan",
        "focus.prefer_climate",
        "focus.preferred_temp_c",
    ],
    "comfort_adjust": [
        "comfort.preferred_temp_c",
        "comfort.prefer_fan",
        "comfort.prefer_climate",
    ],
}


@dataclass
class TieredMemory:
    kv: SqliteKV
    max_recent_episodes: int = 5
    pref_keys_by_intent: dict[str, list[str]] = field(
        default_factory=lambda: dict(DEFAULT_PREF_KEYS_BY_INTENT)
    )

    def relevant_preference_keys(
        self,
        *,
        intent: str | None,
        user_text: str | None = None,
    ) -> list[str]:
        keys: list[str] = []

        if intent and intent in self.pref_keys_by_intent:
            keys.extend(self.pref_keys_by_intent[intent])

        text = (user_text or "").strip().lower()
        if any(word in text for word in ("sleep", "bedtime", "wind down")):
            keys.extend(self.pref_keys_by_intent.get("sleep_mode", []))
        if any(word in text for word in ("focus", "study", "deep work")):
            keys.extend(self.pref_keys_by_intent.get("focus_start", []))
        if any(word in text for word in ("comfort", "comfortable", "cool", "warm")):
            keys.extend(self.pref_keys_by_intent.get("comfort_adjust", []))

        seen: set[str] = set()
        ordered: list[str] = []
        for key in keys:
            if key not in seen:
                seen.add(key)
                ordered.append(key)
        return ordered

    def get_relevant_preferences(
        self,
        *,
        intent: str | None,
        user_text: str | None = None,
        defaults: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prefs = self.kv.get_namespace("prefs")
        defaults = defaults or {}

        out: dict[str, Any] = {}
        for key in self.relevant_preference_keys(intent=intent, user_text=user_text):
            if key in prefs:
                out[key] = prefs[key]
            elif key in defaults:
                out[key] = defaults[key]
        return out

    def get_recent_episodes(self, limit: int | None = None) -> list[dict[str, Any]]:
        raw = self.kv.get("episodes", "recent", [])
        if not isinstance(raw, list):
            return []
        cap = self.max_recent_episodes if limit is None else max(0, int(limit))
        return [ep for ep in raw if isinstance(ep, dict)][:cap]

    def get_last_episode(self) -> dict[str, Any] | None:
        raw = self.kv.get("episodes", "last", None)
        return raw if isinstance(raw, dict) else None

    def get_rolling_summary(self) -> str:
        raw = self.kv.get("episodes", "rolling_summary", "")
        return raw if isinstance(raw, str) else ""

    def record_episode(self, episode: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_episode(episode)

        recent = self.get_recent_episodes(limit=self.max_recent_episodes)
        recent = [normalized, *recent]
        recent = recent[: self.max_recent_episodes]

        summary = self._build_summary(recent)

        self.kv.set("episodes", "last", normalized)
        self.kv.set("episodes", "recent", recent)
        self.kv.set("episodes", "rolling_summary", summary)
        self.kv.append_event(
            "episode_recorded",
            {
                "intent": normalized.get("intent"),
                "execution_success": normalized.get("execution_success"),
                "policy_decision": normalized.get("policy_decision"),
            },
        )
        return normalized

    def _normalize_episode(self, episode: dict[str, Any]) -> dict[str, Any]:
        plan_summary = episode.get("plan_summary", [])
        if not isinstance(plan_summary, list):
            plan_summary = []

        memory_hits = episode.get("memory_hits", [])
        if not isinstance(memory_hits, list):
            memory_hits = []

        normalized = {
            "ts": episode.get("ts"),
            "user_text": str(episode.get("user_text", "") or ""),
            "intent": str(episode.get("intent", "") or ""),
            "memory_hits": [str(x) for x in memory_hits if str(x).strip()],
            "state_snapshot": episode.get("state_snapshot", {})
            if isinstance(episode.get("state_snapshot"), dict)
            else {},
            "plan_summary": [str(x) for x in plan_summary if str(x).strip()],
            "policy_decision": str(episode.get("policy_decision", "") or ""),
            "policy_reason": str(episode.get("policy_reason", "") or ""),
            "execution_success": bool(episode.get("execution_success", False)),
        }
        return normalized

    def _build_summary(self, recent: list[dict[str, Any]]) -> str:
        if not recent:
            return "No recent episodes."

        intents = [
            str(ep.get("intent", "")).strip() for ep in recent if str(ep.get("intent", "")).strip()
        ]
        intent_counts = Counter(intents)
        top_intent = intent_counts.most_common(1)[0][0] if intent_counts else "unknown"

        success_count = sum(1 for ep in recent if bool(ep.get("execution_success", False)))
        latest = recent[0] if recent else {}
        latest_intent = str(latest.get("intent", "unknown") or "unknown")
        latest_policy = str(latest.get("policy_decision", "unknown") or "unknown")
        latest_actions = latest.get("plan_summary", [])
        latest_actions_count = len(latest_actions) if isinstance(latest_actions, list) else 0

        return (
            f"Recent episodes: {len(recent)} total, {success_count} successful. "
            f"Most common intent: {top_intent}. "
            f"Latest episode: {latest_intent} with policy={latest_policy} "
            f"and {latest_actions_count} planned actions."
        )
