from __future__ import annotations

from agent.runner import Runner
from agent.tool_behaviors import (
    DefaultToolBehavior,
    LightSetBehavior,
    ToolBehaviorRegistry,
    TtsSayBehavior,
)
from contracts.ha import ToolCall, ToolResult
from tools.tool_executor import ToolExecutor


def _call(tool: str, args: dict) -> ToolCall:
    return ToolCall(tool=tool, args=args, idempotency_key="k1", correlation_id="cid")


def test_registry_resolves_supported_tools():
    registry = ToolBehaviorRegistry()

    assert isinstance(
        registry.for_call(_call("light.set", {"entity_id": "light.bedroom_light", "state": "off"})),
        LightSetBehavior,
    )
    assert isinstance(registry.for_call(_call("tts.say", {"message": "hi"})), TtsSayBehavior)


def test_light_set_behavior_is_verification_critical():
    registry = ToolBehaviorRegistry()
    behavior = registry.for_call(
        _call("light.set", {"entity_id": "light.bedroom_light", "state": "off"})
    )

    assert behavior.is_verification_critical(
        _call("light.set", {"entity_id": "light.bedroom_light", "state": "off"})
    )


def test_tts_behavior_is_not_retryable():
    registry = ToolBehaviorRegistry()
    call = _call("tts.say", {"message": "hi"})

    assert registry.for_call(call).is_retryable(call) is False


def test_unknown_tool_uses_default_behavior():
    registry = ToolBehaviorRegistry()
    runner = Runner(executor=ToolExecutor(mode="active"))
    call = _call("media.pause", {"entity_id": "media_player.bedroom"})
    result = ToolResult(ok=True, tool=call.tool)
    behavior = registry.for_call(call)

    assert isinstance(behavior, DefaultToolBehavior)
    assert behavior.verify(runner, call, result) == {"verified": True, "note": "no verifier for tool"}
