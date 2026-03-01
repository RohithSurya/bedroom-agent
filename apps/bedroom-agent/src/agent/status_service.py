from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from llm.base import LLMClient
from memory.sqlite_kv import SqliteKV


STATUS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer", "reasoning_tags", "confidence"],
    "properties": {
        "answer": {"type": "string"},
        "reasoning_tags": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
}


@dataclass
class StatusService:
    kv: SqliteKV
    llm: Optional[LLMClient]
    tz_name: str

    def handle_query(self, query: str) -> dict[str, Any]:
        query = (query or "What is the room status?").strip()
        query_type = self._classify(query)
        beliefs = self.kv.get_namespace("belief")
        prefs = self.kv.get_namespace("prefs")
        recent_events = self.kv.recent_events(limit=40)
        relevant_events = self._select_events_for_query(query_type, recent_events)
        context = {
            "query_type": query_type,
            "beliefs": beliefs,
            "prefs": prefs,
            "recent_events": [self._serialize_event(evt) for evt in relevant_events],
        }

        fallback = self._fallback_answer(query_type, beliefs, prefs, relevant_events)
        structured = fallback if query_type in {"why_light_on", "why_light_off"} else self._llm_answer(
            query=query, context=context, fallback=fallback
        )

        result = {
            "summary": structured["answer"],
            "structured": {
                "query_type": query_type,
                "beliefs": beliefs,
                "recent_events": context["recent_events"],
                "reasoning_tags": structured["reasoning_tags"],
                "confidence": structured["confidence"],
            },
        }
        self.kv.set("status", "last_summary", result)
        self.kv.append_event("status_query_answered", {"query": query, "query_type": query_type})
        return result

    def _llm_answer(self, *, query: str, context: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
        if self.llm is None:
            return fallback

        context_json = json.dumps(context, ensure_ascii=False, sort_keys=True)
        prompt = (
            "You explain a bedroom automation agent's behavior using only the supplied JSON context. "
            "Do not invent sensors, actions, or causes. Keep the answer concise and specific. "
            "If the answer is unclear, say so briefly.\n\n"
            f"User query: {query}\n"
            f"Context JSON: {context_json}\n"
            "Return JSON with answer, reasoning_tags, confidence."
        )
        try:
            out = self.llm.generate_json(prompt=prompt, schema=STATUS_SCHEMA, temperature=0.1)
        except Exception:
            return fallback

        answer = out.get("answer")
        tags = out.get("reasoning_tags")
        confidence = out.get("confidence")
        if not isinstance(answer, str) or not answer.strip():
            return fallback
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            return fallback
        if not isinstance(confidence, (int, float)):
            return fallback
        return {
            "answer": answer.strip(),
            "reasoning_tags": tags,
            "confidence": float(confidence),
        }

    def _classify(self, query: str) -> str:
        q = query.lower()
        if "why" in q and ("turn on" in q or "turned on" in q or "light on" in q):
            return "why_light_on"
        if "why" in q and ("turn off" in q or "turned off" in q or "light off" in q):
            return "why_light_off"
        if any(term in q for term in ("recent", "happened", "summary", "what happened")):
            return "recent_events"
        return "room_status"

    def _fallback_answer(
        self,
        query_type: str,
        beliefs: dict[str, Any],
        prefs: dict[str, Any],
        recent_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if query_type == "why_light_on":
            return self._fallback_why_on(recent_events)
        if query_type == "why_light_off":
            return self._fallback_why_off(recent_events)
        if query_type == "recent_events":
            return self._fallback_recent(recent_events)
        return self._fallback_status(beliefs, prefs, recent_events)

    def _fallback_why_on(self, recent_events: list[dict[str, Any]]) -> dict[str, Any]:
        if self._find_event(recent_events, "enter_room_skipped_quiet_hours_switch"):
            return {
                "answer": "The agent detected entry, but skipped turning the switch on because it was quiet hours.",
                "reasoning_tags": ["enter_detected", "quiet_hours", "switch_skip"],
                "confidence": 0.99,
            }
        if self._find_event(recent_events, "enter_room_skipped_already_on"):
            return {
                "answer": "The room entry trigger fired, but the light was already on so no new turn-on was needed.",
                "reasoning_tags": ["enter_detected", "already_on"],
                "confidence": 0.99,
            }
        if self._find_event(recent_events, "enter_detected"):
            return {
                "answer": "The bedroom light turned on because the door opened and presence was detected within the entry window.",
                "reasoning_tags": ["door_open", "presence_detected", "entry_window"],
                "confidence": 0.96,
            }
        if self._find_event(recent_events, "door_update"):
            return {
                "answer": "I saw a recent door-open event, but not enough entry evidence in the retained logs to confirm the exact light-on trigger.",
                "reasoning_tags": ["door_open", "insufficient_entry_evidence"],
                "confidence": 0.7,
            }
        return {
            "answer": "I do not see a recent successful entry trigger for the light turn-on in memory.",
            "reasoning_tags": ["no_recent_enter_detected"],
            "confidence": 0.62,
        }

    def _fallback_why_off(self, recent_events: list[dict[str, Any]]) -> dict[str, Any]:
        vacancy = self._find_event(recent_events, "vacancy_detected")
        if vacancy is not None:
            delay_s = vacancy["payload"].get("delay_s")
            delay_part = f" for {delay_s} seconds" if delay_s is not None else ""
            return {
                "answer": f"The bedroom light turned off because no presence was detected{delay_part}.",
                "reasoning_tags": ["presence_false", "vacancy_timeout", "light_off"],
                "confidence": 0.97,
            }
        if self._find_event(recent_events, "vacancy_off_skipped_already_off"):
            return {
                "answer": "The room became vacant, but the light was already off so no turn-off action was needed.",
                "reasoning_tags": ["presence_false", "already_off"],
                "confidence": 0.96,
            }
        return {
            "answer": "I do not see a recent vacancy-based light-off event in memory.",
            "reasoning_tags": ["no_recent_vacancy_event"],
            "confidence": 0.62,
        }

    def _fallback_recent(self, recent_events: list[dict[str, Any]]) -> dict[str, Any]:
        if not recent_events:
            return {
                "answer": "There are no recent bedroom events stored yet.",
                "reasoning_tags": ["no_recent_events"],
                "confidence": 1.0,
            }

        parts = []
        for event in recent_events[:5]:
            ts = self._format_ts(event["ts"])
            parts.append(f"{ts}: {self._humanize_event(event)}")
        return {
            "answer": "Recent bedroom events: " + " | ".join(parts),
            "reasoning_tags": [event["type"] for event in recent_events[:5]],
            "confidence": 0.94,
        }

    def _fallback_status(
        self,
        beliefs: dict[str, Any],
        prefs: dict[str, Any],
        recent_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        presence = "present" if beliefs.get("presence") else "not present"
        door = "open" if beliefs.get("door_open") else "closed"
        guest_mode = "on" if prefs.get("guest_mode") else "off"
        latest_analysis = self.kv.get("vision", "latest_bedroom_analysis", None)
        analysis_part = ""
        if isinstance(latest_analysis, dict):
            summary = latest_analysis.get("summary")
            if isinstance(summary, str) and summary.strip():
                analysis_part = f" Latest room analysis: {summary.strip()}"

        recent_part = ""
        if recent_events:
            recent_part = f" Most recent event: {self._humanize_event(recent_events[0])}."
        return {
            "answer": (
                f"Current bedroom status: presence is {presence}, the door belief is {door}, "
                f"and guest mode is {guest_mode}.{recent_part}{analysis_part}"
            ).strip(),
            "reasoning_tags": ["presence", "door_open", "guest_mode", "recent_events"],
            "confidence": 0.9,
        }

    def _serialize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": event["type"],
            "ts": event["ts"],
            "at_local": self._format_ts(event["ts"]),
            "payload": event["payload"],
        }

    def _humanize_event(self, event: dict[str, Any]) -> str:
        event_type = event["type"]
        payload = event["payload"]
        if event_type == "door_update":
            state = "open" if payload.get("door_open") else "closed"
            return f"door updated to {state}"
        if event_type == "presence_update":
            state = "present" if payload.get("presence") else "not present"
            return f"presence updated to {state}"
        if event_type == "enter_detected":
            return "entry was detected"
        if event_type == "vacancy_detected":
            delay_s = payload.get("delay_s")
            if delay_s is not None:
                return f"vacancy detected after {delay_s} seconds"
            return "vacancy detected"
        if event_type == "vacancy_off_executed":
            return "the bedroom light was turned off"
        if event_type.startswith("enter_room_skipped_"):
            reason = event_type.removeprefix("enter_room_skipped_").replace("_", " ")
            return f"entry automation skipped because {reason}"
        return event_type.replace("_", " ")

    def _find_event(self, recent_events: list[dict[str, Any]], event_type: str) -> dict[str, Any] | None:
        for event in recent_events:
            if event["type"] == event_type:
                return event
        return None

    def _select_events_for_query(
        self, query_type: str, recent_events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if query_type == "why_light_on":
            selected = self._prioritize_events(
                recent_events,
                preferred_types=[
                    "enter_room_skipped_quiet_hours_switch",
                    "enter_room_skipped_already_on",
                    "enter_detected",
                    "door_update",
                    "presence_update",
                ],
                limit=6,
            )
            return selected

        if query_type == "why_light_off":
            return self._prioritize_events(
                recent_events,
                preferred_types=[
                    "vacancy_off_executed",
                    "vacancy_detected",
                    "vacancy_timer_started",
                    "vacancy_timer_cancelled",
                    "presence_update",
                ],
                limit=6,
            )

        if query_type == "recent_events":
            return self._compress_presence_events(recent_events, limit=6)

        return self._prioritize_events(
            recent_events,
            preferred_types=[
                "enter_detected",
                "vacancy_detected",
                "vacancy_off_executed",
                "door_update",
                "presence_update",
                "bedroom_analysis_completed",
            ],
            limit=6,
        )

    def _prioritize_events(
        self, recent_events: list[dict[str, Any]], *, preferred_types: list[str], limit: int
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        seen_presence_state: set[str] = set()

        for event_type in preferred_types:
            for event in recent_events:
                if event["type"] != event_type:
                    continue
                if event_type == "presence_update":
                    state_key = str(bool(event["payload"].get("presence")))
                    if state_key in seen_presence_state:
                        continue
                    seen_presence_state.add(state_key)
                if event not in selected:
                    selected.append(event)
                if len(selected) >= limit:
                    return selected

        return selected[:limit]

    def _compress_presence_events(
        self, recent_events: list[dict[str, Any]], *, limit: int
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        last_presence: bool | None = None

        for event in recent_events:
            if event["type"] != "presence_update":
                selected.append(event)
            else:
                current_presence = bool(event["payload"].get("presence"))
                if current_presence != last_presence:
                    selected.append(event)
                    last_presence = current_presence
            if len(selected) >= limit:
                break

        return selected[:limit]

    def _format_ts(self, ts: float) -> str:
        return datetime.fromtimestamp(float(ts), ZoneInfo(self.tz_name)).strftime("%Y-%m-%d %H:%M:%S")
