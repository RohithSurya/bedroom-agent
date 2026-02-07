from __future__ import annotations
from typing import Literal

from pydantic import BaseModel, Field


class PolicyDecision(BaseModel):
    decision: Literal["allow", "deny"]
    reason: str
    cooldown_seconds: int = 0
    safety_checks: list[str] = Field(default_factory=list)
