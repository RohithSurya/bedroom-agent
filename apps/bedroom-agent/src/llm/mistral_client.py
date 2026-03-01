from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

import requests


@dataclass
class MistralClient:
    """Minimal Mistral Chat Completions client."""

    api_key: str
    model: str
    base_url: str = "https://api.mistral.ai/v1"
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

    def _build_messages(self, *, prompt: str, images_b64: Optional[list[str]]) -> list[dict[str, Any]]:
        if not images_b64:
            return [{"role": "user", "content": prompt}]

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image_b64 in images_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": f"data:image/jpeg;base64,{image_b64}",
                }
            )
        return [{"role": "user", "content": content}]

    def _extract_content(self, data: dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            return ""

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
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
    ) -> dict[str, Any]:
        if not self.api_key.strip():
            raise ValueError("MISTRAL_API_KEY is required when LLM_PROVIDER=mistral")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(prompt=prompt, images_b64=images_b64),
            "temperature": temperature,
        }
        if num_predict is not None:
            payload["max_tokens"] = int(num_predict)
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "agent_schema", "schema": schema},
            }

        response = requests.post(
            self.base_url.rstrip("/") + "/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        return response.json()

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
        return {
            "response": self._extract_content(data),
            "done_reason": data.get("choices", [{}])[0].get("finish_reason", ""),
            "usage": data.get("usage", {}),
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
