from __future__ import annotations

from agent.tool_registry import TOOL_DEFINITIONS, all_tools, get_tool
from agent.tool_behaviors import ToolBehaviorRegistry


def test_get_tool_returns_definition():
    defn = get_tool("light.set")
    assert defn.name == "light.set"
    assert defn.requires_verification is True
    assert defn.retryable is True


def test_get_tool_unknown_raises():
    try:
        get_tool("nonexistent.tool")
        assert False, "Expected KeyError"
    except KeyError:
        pass


def test_all_tools_returns_all_definitions():
    tools = all_tools()
    assert len(tools) == 7
    names = {t.name for t in tools}
    assert names == {
        "light.set",
        "fan.set",
        "switch.set",
        "climate.set_mode",
        "climate.set_temperature",
        "climate.set_fan_mode",
        "tts.say",
    }


def test_tts_not_retryable():
    defn = get_tool("tts.say")
    assert defn.retryable is False
    assert defn.requires_verification is False


def test_registry_consistent_with_behaviors():
    """Every tool in TOOL_DEFINITIONS has a behavior in ToolBehaviorRegistry."""
    registry = ToolBehaviorRegistry()
    from contracts.ha import ToolCall

    for name, defn in TOOL_DEFINITIONS.items():
        call = ToolCall(tool=name, args={}, idempotency_key="k", correlation_id="c")
        behavior = registry.for_call(call)
        assert behavior.is_retryable(call) == defn.retryable, f"{name} retryable mismatch"
        assert (
            behavior.is_verification_critical(call) == defn.requires_verification
        ), f"{name} verification_critical mismatch"
