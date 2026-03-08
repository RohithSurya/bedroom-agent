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

    # Local or hosted OpenAI-compatible backend (llama.cpp server).
    LLM_BASE_URL: str = "http://127.0.0.1:8081/v1"
    LLM_MODEL: str = "Ministral-3-3B-Instruct-2512-Q4_K_M.gguf"
    LLM_TIMEOUT_S: float = 60.0
    OPENAI_API_KEY: str = ""
    LLM_DECISION_ENABLED: bool = True
    LLM_DECISION_MIN_CONFIDENCE: float = 0.55
    LLM_DECISION_TIMEOUT_S: float = 20.0
    LLM_DECISION_USE_VISION: bool = True
    LLM_DECISION_MAX_EVENTS: int = 8

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
    ENTRY_LIGHT_ENTITY_ID: str = "light.bedroom_light"
    BEDROOM_LAMP_ENTITY_ID: str = "light.bedlamp"
    BEDROOM_FAN_ENTITY_ID: str = "fan.bedroom_fan"
    BEDROOM_AC_ENTITY_ID: str = "climate.bedroom_ac"
    VACANCY_OFF_DELAY_S: int = 120  # turn lights off after this much no-presence time
    TEMP_SENSOR_ENTITY_ID: str = "sensor.temp_humidity_sensor_temperature"
    HUMIDITY_SENSOR_ENTITY_ID: str = "sensor.temp_humidity_sensor_humidity"
    COMFORT_TRIGGER_TEMP_C: float = 25.0
    COMFORT_TRIGGER_HUMIDITY_PCT: float = 65.0
    COMFORT_TARGET_TEMP_C: int = 24
    SLEEP_TARGET_TEMP_C: int = 27
    FOCUS_MODE_ENABLE_FAN: bool = True
    FOCUS_MODE_ENABLE_CLIMATE: bool = True
    SLEEP_MODE_ENABLE_CLIMATE: bool = True
    COMFORT_USE_FAN_FALLBACK: bool = True

    # Vision / snapshot analysis
    CAMERA_MODE: str = "device"  # "ha_snapshot", "file", or "device"
    CAMERA_ENTITY_ID: str = ""
    CAMERA_DEVICE: str = "/dev/video0"
    CAMERA_WIDTH: int = 640
    CAMERA_HEIGHT: int = 480
    CAMERA_SKIP_FRAMES: int = 30
    VISION_FALLBACK_IMAGE_PATH: str = ""
    VISION_DEBUG_SAVE_DIR: str = "/home/rosurya/bedroom-agent/apps/bedroom-agent/data/debug"
    VISION_ANALYSIS_ENABLED: bool = True
    VISION_PROMPT_PROFILE: str = "general"  # "general", "focus", "sleep"
    VISION_MAX_OUTPUT_TOKENS: int = 160

    # Quiet hours (optional but nice)
    QUIET_HOURS_START: str = "00:00"  # HH:MM
    QUIET_HOURS_END: str = "00:00"  # HH:MM

    # Reliability tuning (for evals and real-world robustness)
    REQUEST_BUDGET_S: float = 12.0
