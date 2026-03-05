from __future__ import annotations

from llm.base import LLMClient
from llm.openai_client import OpenAIClient


def build_llm_client(
    *,
    model: str,
    timeout_s: float,
    base_url: str,
    openai_api_key: str = "",
) -> LLMClient:
    return OpenAIClient(
        base_url=base_url,
        model=model,
        api_key=openai_api_key,
        timeout_s=timeout_s,
    )
