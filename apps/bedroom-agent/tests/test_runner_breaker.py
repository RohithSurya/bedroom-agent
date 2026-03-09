from __future__ import annotations

import time

from contracts.ha import ToolCall
from core.logging_jsonl import JsonlLogger
from reliability.circuit_breaker import CircuitBreaker
from reliability.retry import RetryPolicy
from tools.tool_executor import ToolExecutor
from agent.runner import Runner


def test_breaker_short_circuits_after_threshold(tmp_path):
    logger = JsonlLogger(log_dir=str(tmp_path), tz_name="America/New_York")
    ex = ToolExecutor(mode="active")
    ex.inject_failure(tool="fan.set", times=10, error="simulated_timeout")

    runner = Runner(
        executor=ex,
        logger=logger,
        tool_retry_policy=RetryPolicy(
            max_attempts=1, base_delay_s=0.0, max_delay_s=0.0, jitter_s=0.0
        ),
        ha_breaker=CircuitBreaker(failure_threshold=2, recovery_timeout_s=999),
    )

    calls = [
        ToolCall(
            tool="fan.set",
            args={"entity_id": "fan.bedroom_fan", "state": "on"},
            idempotency_key=f"k{i}",
            correlation_id="c1",
        )
        for i in range(4)
    ]

    for c in calls:
        runner._execute_with_transient_retries(c)

    # First 2 attempts hit the executor, then breaker opens and short-circuits.
    assert ex.executions == 2


def test_breaker_recovers_after_timeout(tmp_path):
    logger = JsonlLogger(log_dir=str(tmp_path), tz_name="America/New_York")
    ex = ToolExecutor(mode="active")
    ex.inject_failure(tool="fan.set", times=2, error="simulated_timeout")

    runner = Runner(
        executor=ex,
        logger=logger,
        tool_retry_policy=RetryPolicy(
            max_attempts=1, base_delay_s=0.0, max_delay_s=0.0, jitter_s=0.0
        ),
        ha_breaker=CircuitBreaker(failure_threshold=2, recovery_timeout_s=0.01),
    )

    c1 = ToolCall(
        tool="fan.set",
        args={"entity_id": "fan.bedroom_fan", "state": "on"},
        idempotency_key="k1",
        correlation_id="c2",
    )
    c2 = ToolCall(
        tool="fan.set",
        args={"entity_id": "fan.bedroom_fan", "state": "on"},
        idempotency_key="k2",
        correlation_id="c2",
    )
    c3 = ToolCall(
        tool="fan.set",
        args={"entity_id": "fan.bedroom_fan", "state": "on"},
        idempotency_key="k3",
        correlation_id="c2",
    )

    # opens breaker
    runner._execute_with_transient_retries(c1)
    runner._execute_with_transient_retries(c2)

    # wait for HALF_OPEN eligibility
    time.sleep(0.02)

    r3 = runner._execute_with_transient_retries(c3)
    assert r3.ok is True


class _CountingExecutor:
    mode = "active"

    def __init__(self) -> None:
        self.read_calls = 0

    def read_entity_state(self, entity_id: str) -> dict[str, str]:
        self.read_calls += 1
        return {"entity_id": entity_id, "state": "on", "attributes": {}}


def test_runner_read_entity_state_calls_backend_once():
    runner = Runner(executor=_CountingExecutor())  # type: ignore[arg-type]

    state = runner.read_entity_state("light.bedroom_light")

    assert state["state"] == "on"
    assert runner.executor.read_calls == 1
