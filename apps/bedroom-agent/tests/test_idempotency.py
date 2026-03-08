from __future__ import annotations

from contracts.ha import ToolCall
from tools.tool_executor import ToolExecutor


def test_tool_executor_idempotency_caches_result():
    ex = ToolExecutor(mode="active")

    call = ToolCall(
        tool="light.set",
        args={"entity_id": "light.bedlamp", "brightness_pct": 12, "transition_s": 0},
        idempotency_key="same-key",
        correlation_id="c1",
    )

    r1 = ex.execute(call)
    assert r1.ok is True
    assert r1.details.get("cached") is False
    assert ex.executions == 1

    # execute again with same idempotency_key => cached, no new execution
    r2 = ex.execute(call)
    assert r2.ok is True
    assert r2.details.get("cached") is True
    assert ex.executions == 1
