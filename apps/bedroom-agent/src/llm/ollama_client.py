from __future__ import annotations

import json
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

    def generate_json(
        self,
        *,
        prompt: str,
        schema: Optional[dict[str, Any]] = None,
        images_b64: Optional[list[str]] = None,
        temperature: float = 0.2,
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
        try:
            return json.loads(raw)
        except Exception:
            # Last resort: return a structured error the caller can log.
            return {"_parse_error": True, "raw": raw[:2000]}
