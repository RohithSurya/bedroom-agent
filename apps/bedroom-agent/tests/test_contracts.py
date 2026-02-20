from __future__ import annotations

from contracts.ha import ToolCall
from contracts.policy import PolicyDecision


def test_contracts_validate():
    d = PolicyDecision(decision="allow", reason="ok", cooldown_seconds=10, safety_checks=["x"])
    assert d.decision == "allow"

    tc = ToolCall(
        tool="light.set_scene", args={"scene": "night_dim"}, idempotency_key="k", correlation_id="c"
    )
    assert tc.tool == "light.set_scene"
