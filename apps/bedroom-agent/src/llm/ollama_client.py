from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

import requests


@dataclass
class OllamaClient:
    """Minimal Ollama HTTP client.

    Designed so you can swap out the backend later (e.g., TensorRT-LLM server)
    while keeping the agent-facing interface stable.
    """

    base_url: str
    model: str
    timeout_s: float = 60.0

    def _parse_json_response(self, raw: str) -> dict[str, Any]:
        raw = (raw or "").strip()
        if not raw:
            return {"_parse_error": True, "raw": ""}

        candidates = [raw]

        fenced = re.findall(r"```(?:json)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
        candidates.extend(chunk.strip() for chunk in fenced if chunk.strip())

        start = min((idx for idx in (raw.find("{"), raw.find("[")) if idx != -1), default=-1)
        if start != -1:
            stack: list[str] = []
            closing = {"{": "}", "[": "]"}
            for idx in range(start, len(raw)):
                ch = raw[idx]
                if ch in closing:
                    stack.append(closing[ch])
                elif stack and ch == stack[-1]:
                    stack.pop()
                    if not stack:
                        candidates.append(raw[start : idx + 1].strip())
                        break

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}

        return {"_parse_error": True, "raw": raw[:2000]}

    def generate_raw(
        self,
        *,
        prompt: str,
        images_b64: Optional[list[str]] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if num_predict is not None:
            payload["options"]["num_predict"] = int(num_predict)
        if images_b64:
            payload["images"] = images_b64

        r = requests.post(
            self.base_url.rstrip("/") + "/api/generate",
            json=payload,
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        return r.json()

    def generate_json(
        self,
        *,
        prompt: str,
        schema: Optional[dict[str, Any]] = None,
        images_b64: Optional[list[str]] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
    ) -> dict[str, Any]:
        """Generate a JSON object.

        If `schema` is provided, we pass it to Ollama's structured output feature.
        If `images_b64` is provided, this becomes a VLM call.
        """

        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if num_predict is not None:
            payload["options"]["num_predict"] = int(num_predict)
        if images_b64:
            payload["images"] = images_b64
        if schema is not None:
            # Ollama structured output: format=json or format=<JSON schema object>
            payload["format"] = schema

        r = requests.post(
            self.base_url.rstrip("/") + "/api/generate",
            json=payload,
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        data = r.json()

        # Ollama returns the model output as a string in `response`.
        raw = data.get("response", "").strip()
        return self._parse_json_response(raw)
