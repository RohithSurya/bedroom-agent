from __future__ import annotations

from typing import Any, Optional, Protocol


class LLMClient(Protocol):
    def _parse_json_response(self, raw: str) -> dict[str, Any]:
        ...

    def generate_raw(
        self,
        *,
        prompt: str,
        images_b64: Optional[list[str]] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
    ) -> dict[str, Any]:
        ...

    def generate_json(
        self,
        *,
        prompt: str,
        schema: Optional[dict[str, Any]] = None,
        images_b64: Optional[list[str]] = None,
        temperature: float = 0.2,
        num_predict: Optional[int] = None,
    ) -> dict[str, Any]:
        ...
