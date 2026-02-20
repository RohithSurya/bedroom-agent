from __future__ import annotations

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    tool: str
    args: dict = Field(default_factory=dict)
    idempotency_key: str
    correlation_id: str


class ToolResult(BaseModel):
    ok: bool
    tool: str
    details: dict = Field(default_factory=dict)
