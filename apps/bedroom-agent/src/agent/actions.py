from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from contracts.ha import ToolCall
from core.ids import new_idempotency_key


class AgentAction(Protocol):
    def to_tool_calls(self, correlation_id: str) -> list[ToolCall]:
        ...


def _tool_call(correlation_id: str, *, tool: str, args: dict) -> ToolCall:
    return ToolCall(
        tool=tool,
        args=args,
        idempotency_key=new_idempotency_key(),
        correlation_id=correlation_id,
    )


@dataclass(frozen=True)
class LightAction:
    entity_id: str
    state: str

    def to_tool_calls(self, correlation_id: str) -> list[ToolCall]:
        return [
            _tool_call(
                correlation_id,
                tool="light.set",
                args={"entity_id": self.entity_id, "state": self.state},
            )
        ]


@dataclass(frozen=True)
class FanAction:
    entity_id: str
    state: str

    def to_tool_calls(self, correlation_id: str) -> list[ToolCall]:
        return [
            _tool_call(
                correlation_id,
                tool="fan.set",
                args={"entity_id": self.entity_id, "state": self.state},
            )
        ]


@dataclass(frozen=True)
class SpeechAction:
    message: str

    def to_tool_calls(self, correlation_id: str) -> list[ToolCall]:
        return [_tool_call(correlation_id, tool="tts.say", args={"message": self.message})]


@dataclass(frozen=True)
class ClimatePlan:
    entity_id: str
    hvac_mode: str
    temperature: int | None = None
    fan_mode: str | None = None

    def to_tool_calls(self, correlation_id: str) -> list[ToolCall]:
        calls = [
            _tool_call(
                correlation_id,
                tool="climate.set_mode",
                args={"entity_id": self.entity_id, "hvac_mode": self.hvac_mode},
            )
        ]

        if self.temperature is not None:
            calls.append(
                _tool_call(
                    correlation_id,
                    tool="climate.set_temperature",
                    args={"entity_id": self.entity_id, "temperature": int(self.temperature)},
                )
            )

        if self.fan_mode is not None:
            calls.append(
                _tool_call(
                    correlation_id,
                    tool="climate.set_fan_mode",
                    args={"entity_id": self.entity_id, "fan_mode": self.fan_mode},
                )
            )

        return calls


class ActionFactory:
    def light(self, *, entity_id: str, state: str) -> LightAction:
        return LightAction(entity_id=entity_id, state=state)

    def fan(self, *, entity_id: str, state: str) -> FanAction:
        return FanAction(entity_id=entity_id, state=state)

    def speech(self, *, message: str) -> SpeechAction:
        return SpeechAction(message=message)

    def climate(
        self,
        *,
        entity_id: str,
        hvac_mode: str,
        temperature: int | None = None,
        fan_mode: str | None = None,
    ) -> ClimatePlan:
        return ClimatePlan(
            entity_id=entity_id,
            hvac_mode=hvac_mode,
            temperature=temperature,
            fan_mode=fan_mode,
        )
