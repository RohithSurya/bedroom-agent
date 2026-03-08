from __future__ import annotations

from contracts.ha import ToolCall
from tools.tool_executor import ToolExecutor


def test_injected_failure_is_transient_and_not_cached_by_default():
    ex = ToolExecutor(mode="active")
    ex.inject_failure(tool="light.set", times=1, error="simulated_timeout", cache_failures=False)

    call = ToolCall(
        tool="light.set",
        args={"entity_id": "light.bedlamp", "brightness_pct": 11, "transition_s": 0},
        idempotency_key="same-key",
        correlation_id="c1",
    )

    r1 = ex.execute(call)
    assert r1.ok is False
    assert r1.details.get("injected") is True
    assert ex.executions == 1

    # same idempotency key should execute again (since failure wasn't cached)
    r2 = ex.execute(call)
    assert r2.ok is True
    assert ex.device_state["lights"]["light.bedlamp"]["brightness_pct"] == 11
    assert ex.executions == 2
