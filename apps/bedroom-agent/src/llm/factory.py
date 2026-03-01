from __future__ import annotations

from llm.base import LLMClient
from llm.mistral_client import MistralClient
from llm.ollama_client import OllamaClient


def build_llm_client(
    *,
    provider: str,
    model: str,
    timeout_s: float,
    base_url: str,
    mistral_api_key: str = "",
    mistral_api_base_url: str = "https://api.mistral.ai/v1",
) -> LLMClient:
    normalized = (provider or "ollama").strip().lower()
    if normalized == "mistral":
        return MistralClient(
            api_key=mistral_api_key,
            base_url=mistral_api_base_url,
            model=model,
            timeout_s=timeout_s,
        )
    if normalized != "ollama":
        raise ValueError(f"Unsupported LLM provider: {provider}")
    return OllamaClient(base_url=base_url, model=model, timeout_s=timeout_s)
