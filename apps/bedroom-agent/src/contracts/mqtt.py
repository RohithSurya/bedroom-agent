from __future__ import annotations

from pydantic import BaseModel, Field


class MqttEvent(BaseModel):
    topic: str
    payload: dict = Field(default_factory=dict)
    ts: str  # ISO timestamp string (producer-defined)
