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

    # SQLite (belief state + lightweight memory)
    SQLITE_PATH: str = "data/memory.sqlite"

    # MQTT (Zigbee2MQTT)
    MQTT_HOST: str = "host.docker.internal"
    MQTT_PORT: int = 1883
    MQTT_USERNAME: str | None = None
    MQTT_PASSWORD: str | None = None
    MQTT_TOPIC_PREFIX: str = "zigbee2mqtt"

    # Device topics (customize these to your Z2M topic names)
    Z2M_DOOR_TOPIC: str = "zigbee2mqtt/bedroom_door_sensor"
    Z2M_PRESENCE_TOPIC: str = "zigbee2mqtt/bedroom_mmwave_sensor"

    # Enter-room behavior
    ENTRY_WINDOW_S: int = 12  # door open -> presence true within this window triggers
    ENTRY_COOLDOWN_S: int = 90  # minimum time between triggers
    ENTRY_LIGHT_ENTITY_ID: str = "switch.bedroom_light_switch"
    VACANCY_OFF_DELAY_S: int = 120  # turn lights off after this much no-presence time

    # Quiet hours (optional but nice)
    QUIET_HOURS_START: str = "00:00"  # HH:MM
    QUIET_HOURS_END: str = "00:00"  # HH:MM
