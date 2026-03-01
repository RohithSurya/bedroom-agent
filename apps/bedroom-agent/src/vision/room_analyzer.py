from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Optional

from llm.ollama_client import OllamaClient
from memory.sqlite_kv import SqliteKV
from vision.image_source import BedroomImageSource


ROOM_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "occupied",
        "bed_state",
        "desk_state",
        "focus_readiness",
        "sleep_readiness",
        "issues",
        "query_answer",
        "summary",
    ],
    "properties": {
        "occupied": {"type": "boolean"},
        "bed_state": {"type": "string", "enum": ["made", "partial", "unmade"]},
        "desk_state": {"type": "string", "enum": ["tidy", "active", "cluttered"]},
        "focus_readiness": {"type": "number"},
        "sleep_readiness": {"type": "number"},
        "issues": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "query_answer": {"type": "string", "maxLength": 160},
        "summary": {"type": "string", "maxLength": 180},
    },
}

ROOM_ANALYSIS_SIMPLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["bed_state", "desk_state", "issues", "summary"],
    "properties": {
        "bed_state": {"type": "string"},
        "desk_state": {"type": "string"},
        "issues": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "summary": {"type": "string", "maxLength": 180},
    },
}


@dataclass
class BedroomRoomAnalyzer:
    kv: SqliteKV
    llm: Optional[OllamaClient]
    image_source: BedroomImageSource
    enabled: bool = True
    prompt_profile: str = "general"
    max_output_tokens: int = 120

    def analyze(self, query: str) -> dict[str, Any]:
        if not self.enabled:
            result = self._failure_result("Vision analysis is disabled in configuration.")
            self.kv.append_event("bedroom_analysis_failed", {"reason": "vision_disabled"})
            return result

        image = self.image_source.get_bedroom_image()
        if not image.get("ok"):
            result = self._failure_result(
                f"I could not get a bedroom image for analysis. {image.get('detail', 'no image source available')}"
            )
            self.kv.append_event(
                "bedroom_analysis_failed", {"reason": image.get("detail", "no_image")}
            )
            return result

        structured = self._llm_analysis(query=query, image=image)
        if structured is None:
            result = self._failure_result(
                f"I retrieved a bedroom image from {image['source']}, but vision analysis is unavailable right now."
            )
            self.kv.append_event(
                "bedroom_analysis_failed",
                {"reason": "vision_model_unavailable", "source": image["source"]},
            )
            return result

        stored = {
            **structured,
            "source": image["source"],
            "detail": image.get("detail", ""),
            "query": query,
        }
        for key in ("device", "captured_at_ms", "debug_path", "image_sha256", "path"):
            if key in image and image[key] is not None:
                stored[key] = image[key]
        self.kv.set("vision", "latest_bedroom_analysis", stored)
        self.kv.append_event(
            "bedroom_analysis_completed",
            {
                "source": image["source"],
                "detail": image.get("detail", ""),
                "profile": self.prompt_profile,
                "captured_at_ms": image.get("captured_at_ms"),
                "debug_path": image.get("debug_path"),
                "image_sha256": image.get("image_sha256"),
            },
        )
        response_summary = (
            structured["query_answer"] if self._is_specific_query(query) else structured["summary"]
        )
        return {"summary": response_summary, "structured": stored}

    def _llm_analysis(self, *, query: str, image: dict[str, Any]) -> dict[str, Any] | None:
        if self.llm is None:
            return None

        prompt = self._build_prompt(query)
        image_b64 = base64.b64encode(image["image_bytes"]).decode("ascii")
        try:
            raw = self.llm.generate_raw(
                prompt=prompt,
                images_b64=[image_b64],
                temperature=0.1,
                num_predict=max(self.max_output_tokens, 120),
            )
        except Exception:
            return None

        out = self.llm._parse_json_response(str(raw.get("response", "")))
        if (
            isinstance(out, dict)
            and out.get("_parse_error")
            and str(raw.get("done_reason", "")) == "length"
        ):
            out = self._retry_with_schema(prompt=prompt, image_b64=image_b64)

        out = self._coerce_analysis(out, query=query)
        if self._is_low_signal(out, query):
            out = self._retry_simple_summary(query=query, image_b64=image_b64)

        if not self._valid_analysis(out):
            return None
        normalized = {
            "occupied": bool(out["occupied"]),
            "bed_state": self._normalize_bed_state(out["bed_state"]),
            "desk_state": self._normalize_desk_state(out["desk_state"]),
            "focus_readiness": self._normalize_score(out["focus_readiness"]),
            "sleep_readiness": self._normalize_score(out["sleep_readiness"]),
            "issues": self._sanitize_issues(out["issues"]),
            "query_answer": str(out["query_answer"]).strip(),
            "summary": str(out["summary"]).strip(),
        }
        if not self._is_specific_query(query):
            normalized["focus_readiness"] = self._compute_focus_readiness(normalized)
            normalized["sleep_readiness"] = self._compute_sleep_readiness(normalized)
            normalized["summary"] = self._build_generic_summary(normalized)
            normalized["query_answer"] = normalized["summary"]
        return normalized

    def _retry_with_schema(self, *, prompt: str, image_b64: str) -> dict[str, Any]:
        if self.llm is None:
            return {}
        try:
            return self.llm.generate_json(
                prompt=prompt,
                schema=ROOM_ANALYSIS_SCHEMA,
                images_b64=[image_b64],
                temperature=0.1,
                num_predict=max(self.max_output_tokens, 140),
            )
        except Exception:
            return {}

    def _retry_simple_summary(self, *, query: str, image_b64: str) -> dict[str, Any]:
        if self.llm is None:
            return {}
        prompt = (
            "Analyze one bedroom image and return grounded JSON only. "
            f"User query: {query}. "
            "Use short labels only. "
            "bed_state can be made, partial, or unmade. "
            "Count a bed as made if a comforter or duvet neatly covers most of the mattress, even if pillows are simple or minimal. "
            "Use partial only if bedding is folded back, bunched, or part of the mattress is clearly exposed. "
            "desk_state can be tidy, active, or cluttered. "
            "issues must be at most 3 short phrases. "
            "summary must be one short sentence about the visible room."
        )
        try:
            out = self.llm.generate_json(
                prompt=prompt,
                schema=ROOM_ANALYSIS_SIMPLE_SCHEMA,
                images_b64=[image_b64],
                temperature=0.1,
                num_predict=max(self.max_output_tokens, 120),
            )
        except Exception:
            return {}
        coerced = self._coerce_analysis(out, query=query)
        if not coerced.get("query_answer"):
            coerced["query_answer"] = coerced.get("summary", "")
        if coerced.get("focus_readiness", 0.0) == 0.0 and coerced.get("desk_state") == "active":
            coerced["focus_readiness"] = 0.6
        if coerced.get("sleep_readiness", 0.0) == 0.0 and coerced.get("bed_state") in {
            "made",
            "partial",
        }:
            coerced["sleep_readiness"] = 0.4 if coerced["bed_state"] == "made" else 0.2
        return coerced

    def _valid_analysis(self, out: dict[str, Any]) -> bool:
        if not isinstance(out, dict):
            return False
        if not isinstance(out.get("occupied"), bool):
            return False
        if not isinstance(out.get("bed_state"), str):
            return False
        if not isinstance(out.get("desk_state"), str):
            return False
        if not isinstance(out.get("focus_readiness"), (int, float)):
            return False
        if not isinstance(out.get("sleep_readiness"), (int, float)):
            return False
        if not isinstance(out.get("issues"), list) or not all(
            isinstance(issue, str) for issue in out["issues"]
        ):
            return False
        if not isinstance(out.get("query_answer"), str) or not out["query_answer"].strip():
            return False
        if not isinstance(out.get("summary"), str) or not out["summary"].strip():
            return False
        if self._is_low_signal(out, out.get("query", "")):
            return False
        return True

    def _build_prompt(self, query: str) -> str:
        return (
            "Analyze one bedroom image and return grounded JSON only. "
            "Use short labels. Do not infer identity, age, or hidden objects. "
            f"Prompt profile: {self.prompt_profile}. User query: {query}\n"
            "Rules: bed_state must be made, partial, or unmade. "
            "A bed with a comforter or duvet neatly covering most of the mattress counts as made, including hotel-style simple bedding. "
            "Use partial only when bedding is visibly folded back, bunched up, or part of the mattress is exposed. "
            "desk_state must be tidy, active, or cluttered. "
            "focus_readiness and sleep_readiness must be numbers from 0.0 to 1.0. "
            "issues must contain at most 3 short phrases. "
            "query_answer must answer the user's question directly in one short sentence based only on visible evidence. "
            "summary must be one short sentence describing overall room state."
        )

    def _coerce_analysis(self, out: Any, *, query: str) -> dict[str, Any]:
        if not isinstance(out, dict):
            return {}

        summary = str(out.get("summary", "")).strip()
        query_answer = str(out.get("query_answer", "")).strip()
        answer_text = " ".join(part for part in (query_answer, summary) if part).lower()

        issues = out.get("issues", [])
        if isinstance(issues, str):
            issues = [issues]
        elif not isinstance(issues, list):
            issues = []

        occupied = out.get("occupied")
        if not isinstance(occupied, bool):
            occupied = self._infer_occupied(answer_text, issues)
        if self.kv.get("belief", "presence", False):
            occupied = True

        bed_state = self._normalize_bed_state(
            out.get("bed_state", ""), answer_text=answer_text, issues=issues, query=query
        )

        coerced = {
            "occupied": occupied,
            "bed_state": bed_state,
            "desk_state": self._normalize_desk_state(out.get("desk_state", "")),
            "focus_readiness": self._normalize_score(out.get("focus_readiness", 0.0)),
            "sleep_readiness": self._normalize_score(out.get("sleep_readiness", 0.0)),
            "issues": [str(issue).strip()[:80] for issue in issues[:3] if str(issue).strip()],
            "query_answer": query_answer or summary,
            "summary": summary or query_answer,
        }
        return coerced

    def _infer_occupied(self, answer_text: str, issues: list[Any]) -> bool:
        issue_text = " ".join(str(issue).lower() for issue in issues)
        combined = f"{answer_text} {issue_text}"
        if any(
            phrase in combined for phrase in ("no person", "nobody", "empty room", "not occupied")
        ):
            return False
        if any(
            phrase in combined
            for phrase in (
                "person visible",
                "someone visible",
                "occupied",
                "person at desk",
                "person on bed",
            )
        ):
            return True
        return False

    def _is_low_signal(self, out: dict[str, Any], query: str) -> bool:
        if not isinstance(out, dict):
            return True
        summary = str(out.get("summary", "")).strip()
        query_answer = str(out.get("query_answer", "")).strip()
        issues = out.get("issues", [])
        bed_state = str(out.get("bed_state", "")).strip().lower()
        desk_state = str(out.get("desk_state", "")).strip().lower()
        focus = out.get("focus_readiness", 0.0)
        sleep = out.get("sleep_readiness", 0.0)

        if not summary and not query_answer:
            return True
        if self._is_specific_query(query):
            return not query_answer
        return not summary or (
            not issues
            and bed_state == "made"
            and desk_state == "tidy"
            and float(focus or 0.0) == 0.0
            and float(sleep or 0.0) == 0.0
        )

    def _is_specific_query(self, query: str) -> bool:
        q = (query or "").strip().lower()
        generic_markers = (
            "analyze my room",
            "analyze bedroom",
            "analyze the room",
            "check bedroom",
            "is this room good for focus",
            "what should i fix before sleep",
        )
        return not any(marker in q for marker in generic_markers)

    def _normalize_score(self, value: Any) -> float:
        try:
            numeric = float(value)
        except Exception:
            return 0.0
        if numeric > 1.0:
            numeric = numeric / 5.0
        return max(0.0, min(1.0, numeric))

    def _normalize_bed_state(
        self,
        value: Any,
        *,
        answer_text: str = "",
        issues: list[Any] | None = None,
        query: str = "",
    ) -> str:
        text = str(value).strip().lower()
        issue_text = " ".join(str(issue).strip().lower() for issue in (issues or []))
        query_text = str(query).strip().lower()
        evidence_text = f"{answer_text} {issue_text}".strip()
        combined = f"{text} {evidence_text} {query_text}"

        neat_markers = (
            "neatly covered",
            "neatly made",
            "well made",
            "hotel-style",
            "hotel style",
            "comforter neatly",
            "duvet neatly",
            "fully covered",
            "smooth comforter",
            "blanket neatly",
            "comforter on bed",
            "duvet on bed",
        )
        strong_unmade_markers = (
            "mattress exposed",
            "sheet exposed",
            "bunched",
            "folded back",
            "rumpled",
            "messy bed",
            "pillow scattered",
            "blanket on floor",
            "bedding on floor",
        )
        partial_markers = (
            "partially made",
            "partially covered",
            "needs straightening",
            "fold and flatten",
            "straighten blanket",
            "minor bedding adjustment",
        )

        if any(marker in evidence_text for marker in neat_markers) and not any(
            marker in evidence_text for marker in strong_unmade_markers
        ):
            return "made"

        if "unmade" in text:
            if any(marker in evidence_text for marker in strong_unmade_markers):
                return "unmade"
            if self._is_bed_advice_query(query_text) and not any(
                marker in evidence_text for marker in strong_unmade_markers
            ):
                return "made"
            return "partial"

        if text in {"made", "partial", "unmade"}:
            return text
        if "made" in text and "partial" not in text and "partially" not in text:
            return "made"
        if "partial" in text or "partially" in text:
            if self._is_bed_advice_query(query_text) and not any(
                marker in evidence_text for marker in strong_unmade_markers
            ):
                return "made"
            return "partial"
        if any(marker in evidence_text for marker in partial_markers):
            if self._is_bed_advice_query(query_text) and not any(
                marker in evidence_text for marker in strong_unmade_markers
            ):
                return "made"
            return "partial"
        return "made"

    def _is_bed_advice_query(self, query: str) -> bool:
        q = (query or "").strip().lower()
        return any(
            phrase in q
            for phrase in (
                "make the bed",
                "complete the bed",
                "make complete the bed",
                "how to make the bed",
                "what to do to make",
                "fix the bed",
                "improve the bed",
            )
        )

    def _normalize_desk_state(self, value: Any) -> str:
        text = str(value).strip().lower()
        if text in {"tidy", "active", "cluttered"}:
            return text
        if "clutter" in text or "mess" in text:
            return "cluttered"
        if "active" in text or "workspace" in text or "working" in text:
            return "active"
        return "tidy"

    def _failure_result(self, message: str) -> dict[str, Any]:
        return {
            "summary": message,
            "structured": {
                "available": False,
                "summary": message,
                "issues": [],
            },
        }

    def _sanitize_issues(self, issues: list[Any]) -> list[str]:
        cleaned: list[str] = []
        skip_markers = (
            "ergonomic",
            "storage solution",
            "storage organization",
            "lack of storage",
            "organization in corner",
            "minimal lighting",
            "visible footwear",
            "footwear on floor",
            "possible ",
            "likely ",
            "concern",
        )
        for issue in issues[:3]:
            text = str(issue).strip()[:80]
            lowered = text.lower()
            if not text:
                continue
            if any(marker in lowered for marker in skip_markers):
                continue
            cleaned.append(text)
        return cleaned

    def _build_generic_summary(self, out: dict[str, Any]) -> str:
        desk_state = str(out.get("desk_state", "active")).strip().lower()
        bed_state = str(out.get("bed_state", "made")).strip().lower()
        occupied = bool(out.get("occupied", False))
        parts: list[str] = []

        parts.append("Room appears occupied." if occupied else "Room appears unoccupied.")

        desk_map = {
            "tidy": "The desk looks tidy.",
            "active": "The desk looks actively in use.",
            "cluttered": "The desk looks cluttered.",
        }
        bed_map = {
            "made": "The bed looks neatly made.",
            "partial": "The bed is partially covered but generally neat.",
            "unmade": "The bed looks unmade.",
        }
        parts.append(desk_map.get(desk_state, "The desk is visible."))
        parts.append(bed_map.get(bed_state, "The bed is visible."))

        issues = out.get("issues", [])
        if isinstance(issues, list) and issues:
            parts.append(f"Visible issue: {str(issues[0]).strip()}.")

        return " ".join(parts)

    def _compute_focus_readiness(self, out: dict[str, Any]) -> float:
        desk_state = str(out.get("desk_state", "active")).strip().lower()
        issues = [
            str(issue).strip().lower() for issue in out.get("issues", []) if str(issue).strip()
        ]

        base = {
            "tidy": 0.8,
            "active": 0.6,
            "cluttered": 0.35,
        }.get(desk_state, 0.5)

        focus_penalty_markers = (
            "desk clutter",
            "lack of organization",
            "laundry",
            "clutter",
            "bright lighting",
        )
        penalties = sum(
            0.1 for issue in issues if any(marker in issue for marker in focus_penalty_markers)
        )
        return max(0.0, min(1.0, round(base - penalties, 2)))

    def _compute_sleep_readiness(self, out: dict[str, Any]) -> float:
        bed_state = str(out.get("bed_state", "made")).strip().lower()
        issues = [
            str(issue).strip().lower() for issue in out.get("issues", []) if str(issue).strip()
        ]
        occupied = bool(out.get("occupied", False))

        base = {
            "made": 0.75,
            "partial": 0.55,
            "unmade": 0.25,
        }.get(bed_state, 0.5)

        sleep_penalty_markers = (
            "bright lighting",
            "laundry",
            "clutter near bed",
            "blanket on floor",
            "bedding on floor",
            "messy bed",
            "unmade bed",
        )
        penalties = sum(
            0.1 for issue in issues if any(marker in issue for marker in sleep_penalty_markers)
        )
        if occupied:
            penalties += 0.05
        return max(0.0, min(1.0, round(base - penalties, 2)))
