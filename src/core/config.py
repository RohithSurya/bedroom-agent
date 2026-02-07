from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TIMEZONE: str = "America/New_York"
    LOG_DIR: str = "logs"
    AGENT_MODE: str = "shadow"  # "shadow" or "active"
