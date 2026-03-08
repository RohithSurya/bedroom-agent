from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from contracts.ha import ToolCall, ToolResult

if TYPE_CHECKING:
    from agent.runner import Runner


class ToolBehavior(Protocol):
    def verify(self, runner: Runner, call: ToolCall, result: ToolResult) -> dict[str, Any]: ...

    def is_retryable(self, call: ToolCall) -> bool: ...

    def is_verification_critical(self, call: ToolCall) -> bool: ...


class BaseToolBehavior:
    def verify(self, runner: Runner, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        if getattr(runner.executor, "mode", "active") == "shadow":
            return {
                "verified": bool(result.ok),
                "mode": "shadow",
                "note": "state verification skipped",
            }
        return self._verify_active(runner, call, result)

    def is_retryable(self, call: ToolCall) -> bool:
        return True

    def is_verification_critical(self, call: ToolCall) -> bool:
        return False

    def _verify_active(self, runner: Runner, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        return {"verified": bool(result.ok), "note": "no verifier for tool"}


class LightSetBehavior(BaseToolBehavior):
    def is_verification_critical(self, call: ToolCall) -> bool:
        return True

    def _verify_active(self, runner: Runner, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        entity_id = str(call.args.get("entity_id", "light.bedroom_light"))
        want = str(call.args.get("state", "")).lower()
        ent = runner._read_entity_state(entity_id)
        got = str(ent.get("state", "")).lower()
        return {
            "verified": bool(result.ok) and (got == want),
            "entity_id": entity_id,
            "want": want,
            "got": got,
        }


class FanSetBehavior(BaseToolBehavior):
    def _verify_active(self, runner: Runner, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        entity_id = str(call.args.get("entity_id", "fan.bedroom_fan"))
        want = str(call.args.get("state", "")).lower()
        ent = runner._read_entity_state(entity_id)
        got = str(ent.get("state", "")).lower()
        return {
            "verified": bool(result.ok) and (got == want),
            "entity_id": entity_id,
            "want": want,
            "got": got,
        }


class SwitchSetBehavior(BaseToolBehavior):
    def _verify_active(self, runner: Runner, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        entity_id = str(call.args.get("entity_id", ""))
        want = str(call.args.get("state", "")).lower()
        ent = runner._read_entity_state(entity_id)
        got = str(ent.get("state", "")).lower()
        return {
            "verified": bool(result.ok) and (got == want),
            "entity_id": entity_id,
            "want": want,
            "got": got,
        }


class ClimateSetModeBehavior(BaseToolBehavior):
    def _verify_active(self, runner: Runner, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        entity_id = str(call.args.get("entity_id", "climate.bedroom_ac"))
        want = str(call.args.get("hvac_mode", "")).lower()
        ent = runner._read_entity_state(entity_id)
        attrs = ent.get("attributes", {}) or {}
        got = str(attrs.get("hvac_mode", ent.get("state", ""))).lower()
        return {
            "verified": bool(result.ok) and (got == want),
            "entity_id": entity_id,
            "want": want,
            "got": got,
        }


class ClimateSetTemperatureBehavior(BaseToolBehavior):
    def _verify_active(self, runner: Runner, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        entity_id = str(call.args.get("entity_id", "climate.bedroom_ac"))
        want = call.args.get("temperature")
        ent = runner._read_entity_state(entity_id)
        attrs = ent.get("attributes", {}) or {}
        got = attrs.get("temperature")
        return {
            "verified": bool(result.ok) and (got == want),
            "entity_id": entity_id,
            "want": want,
            "got": got,
        }


class ClimateSetFanModeBehavior(BaseToolBehavior):
    def _verify_active(self, runner: Runner, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        entity_id = str(call.args.get("entity_id", "climate.bedroom_ac"))
        want = str(call.args.get("fan_mode", "")).lower()
        ent = runner._read_entity_state(entity_id)
        attrs = ent.get("attributes", {}) or {}
        got = str(attrs.get("fan_mode", "")).lower()
        return {
            "verified": bool(result.ok) and (got == want),
            "entity_id": entity_id,
            "want": want,
            "got": got,
        }


class TtsSayBehavior(BaseToolBehavior):
    def is_retryable(self, call: ToolCall) -> bool:
        return False

    def _verify_active(self, runner: Runner, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        msg = str(call.args.get("message", ""))

        if not hasattr(runner.executor, "read_entity_state"):
            state = runner.executor.get_state()
            tts = state.get("tts", [])
            return {
                "verified": bool(result.ok) and (len(tts) > 0) and (tts[-1] == msg),
                "message": msg,
            }

        return {
            "verified": bool(result.ok),
            "message": msg,
            "note": "no_state_verifier_for_tts_backend",
        }


class DefaultToolBehavior(BaseToolBehavior):
    pass


class ToolBehaviorRegistry:
    def __init__(self) -> None:
        self._default = DefaultToolBehavior()
        self._by_tool: dict[str, ToolBehavior] = {
            "light.set": LightSetBehavior(),
            "fan.set": FanSetBehavior(),
            "switch.set": SwitchSetBehavior(),
            "climate.set_mode": ClimateSetModeBehavior(),
            "climate.set_temperature": ClimateSetTemperatureBehavior(),
            "climate.set_fan_mode": ClimateSetFanModeBehavior(),
            "tts.say": TtsSayBehavior(),
        }

    def for_call(self, call: ToolCall) -> ToolBehavior:
        return self._by_tool.get(call.tool, self._default)
