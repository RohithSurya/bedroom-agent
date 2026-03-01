from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TIMEZONE: str = "America/New_York"
    LOG_DIR: str = "logs"
    AGENT_MODE: str = "shadow"  # "shadow" or "active"

    TOOL_BACKEND: str = "ha"  # "local" or "http" or "ha" (real Home Assistant client)
    HA_BASE_URL: str = "http://host.docker.internal:8123"
    HA_TOKEN: str = ""

    # Local LLM/VLM backend (hackathon): e.g., Ollama, TensorRT-LLM server, etc.
    LLM_BASE_URL: str = "http://host.docker.internal:11434"
    LLM_MODEL: str = "ministral-3:3b"
    LLM_TIMEOUT_S: float = 60.0
