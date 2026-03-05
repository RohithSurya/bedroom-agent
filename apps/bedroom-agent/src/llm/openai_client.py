from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from openai import OpenAI


@dataclass
class OpenAIClient:
    """OpenAI-compatible Chat Completions client (including llama.cpp server mode)."""

    base_url: str
    model: str
    api_key: str = ""
    timeout_s: float = 60.0
    _client: OpenAI = field(init=False, repr=False)

    def __post_init__(self) -> None:
        base = self.base_url.rstrip("/")
        if not base.endswith("/v1"):
            base = f"{base}/v1"
        self._client = OpenAI(
            base_url=base,
            # OpenAI client requires an API key field even for local compatible servers.
            api_key=(self.api_key or "").strip() or "not-needed",
            timeout=self.timeout_s,
        )

    def _parse_json_response(self, raw: str) -> dict[str, Any]:
        raw = (raw or "").strip()
        if not raw:
            return {"_parse_error": True, "raw": ""}

        # Keep validation simple:
        # 1) parse the whole response
        # 2) if that fails, parse JSON inside a fenced block
        candidate = raw
        try:
            parsed = json.loads(candidate)
        except Exception:
            match = re.search(r"```(?:json)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
            if not match:
                return {"_parse_error": True, "raw": raw[:2000]}
            candidate = match.group(1).strip()
            try:
                parsed = json.loads(candidate)
            except Exception:
                return {"_parse_error": True, "raw": raw[:2000]}

        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}

    def _build_messages(
        self, *, prompt: str, images_b64: Optional[list[str]]
    ) -> list[dict[str, Any]]:
        if not images_b64:
            return [{"role": "user", "content": prompt}]

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_b64 in images_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                }
            )
        return [{"role": "user", "content": content}]

    def _extract_content(self, data: Any) -> str:
        try:
            content = data.choices[0].message.content
        except Exception:
            return ""

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                else:
                    text = getattr(item, "text", None)
                if isinstance(text, str) and text.strip():
                    parts.append(text)
            return "\n".join(parts)
        return str(content or "")

    def _chat_completion(
        self,
        *,
        prompt: str,
        images_b64: Optional[list[str]] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
        schema: Optional[dict[str, Any]] = None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(prompt=prompt, images_b64=images_b64),
            "temperature": float(temperature),
        }
        if num_predict is not None:
            kwargs["max_tokens"] = int(num_predict)

        if schema is None:
            return self._client.chat.completions.create(**kwargs)

        # Try strict JSON schema first, then fall back for compatibility.
        attempts = [
            {"type": "json_schema", "json_schema": {"name": "agent_schema", "schema": schema}},
            {"type": "json_object"},
            None,
        ]
        last_exc: Exception | None = None
        for response_format in attempts:
            try:
                call_kwargs = dict(kwargs)
                if response_format is not None:
                    call_kwargs["response_format"] = response_format
                return self._client.chat.completions.create(**call_kwargs)
            except Exception as exc:
                last_exc = exc

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("LLM chat completion failed unexpectedly")

    def generate_raw(
        self,
        *,
        prompt: str,
        images_b64: Optional[list[str]] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
    ) -> dict[str, Any]:
        data = self._chat_completion(
            prompt=prompt,
            images_b64=images_b64,
            temperature=temperature,
            num_predict=num_predict,
        )
        done_reason = ""
        usage: dict[str, Any] = {}
        try:
            done_reason = str(data.choices[0].finish_reason or "")
        except Exception:
            done_reason = ""
        try:
            raw_usage = data.usage
            usage = raw_usage.model_dump() if hasattr(raw_usage, "model_dump") else dict(raw_usage)
        except Exception:
            usage = {}
        return {
            "response": self._extract_content(data),
            "done_reason": done_reason,
            "usage": usage,
        }

    def generate_json(
        self,
        *,
        prompt: str,
        schema: Optional[dict[str, Any]] = None,
        images_b64: Optional[list[str]] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
    ) -> dict[str, Any]:
        data = self._chat_completion(
            prompt=prompt,
            images_b64=images_b64,
            temperature=temperature,
            num_predict=num_predict,
            schema=schema,
        )
        raw = self._extract_content(data).strip()
        return self._parse_json_response(raw)
