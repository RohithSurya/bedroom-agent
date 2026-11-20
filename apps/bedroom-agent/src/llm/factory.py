from __future__ import annotations

from typing import Any

from llm.base import LLMClient
from llm.openai_client import OpenAIClient


def build_llm_client(
    *,
    model: str,
    timeout_s: float,
    base_url: str,
    openai_api_key: str = "",
    chat_template_kwargs: dict[str, Any] | None = None,
) -> LLMClient:
    client = OpenAIClient(
        base_url=base_url,
        model=model,
        api_key=openai_api_key,
        timeout_s=timeout_s,
    )
    if chat_template_kwargs is not None:
        client.chat_template_kwargs = chat_template_kwargs
    return client
