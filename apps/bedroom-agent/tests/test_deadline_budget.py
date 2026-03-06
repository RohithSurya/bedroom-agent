from reliability.deadline import Deadline
from reliability.retry import RetryPolicy
from core.logging_jsonl import JsonlLogger
from tools.tool_executor import ToolExecutor
from agent.runner import Runner
from contracts.ha import ToolCall


def test_runner_returns_deadline_exceeded_before_any_tool_exec(tmp_path):
    logger = JsonlLogger(log_dir=str(tmp_path), tz_name="America/New_York")
    ex = ToolExecutor(mode="active")
    runner = Runner(executor=ex, logger=logger)

    actions = [
        ToolCall(
            tool="switch.set",
            args={"entity_id": "switch.x", "state": "on"},
            idempotency_key="k1",
            correlation_id="c1",
        )
    ]
    deadline = Deadline.from_now(0.0)

    out = runner.execute_actions(correlation_id="c1", actions=actions, deadline=deadline)
    assert out["success"] is False
    assert any(f.get("reason") == "deadline_exceeded" for f in out["failures"])
    assert ex.executions == 0


def test_deadline_caps_retries(tmp_path):
    logger = JsonlLogger(log_dir=str(tmp_path), tz_name="America/New_York")
    ex = ToolExecutor(mode="active")
    ex.inject_failure(tool="switch.set", times=10, error="simulated_timeout")

    runner = Runner(
        executor=ex,
        logger=logger,
        tool_retry_policy=RetryPolicy(
            max_attempts=5, base_delay_s=0.05, max_delay_s=0.1, jitter_s=0.0
        ),
    )

    actions = [
        ToolCall(
            tool="switch.set",
            args={"entity_id": "switch.x", "state": "on"},
            idempotency_key="k1",
            correlation_id="c2",
        )
    ]
    deadline = Deadline.from_now(0.01)  # too small to allow multiple sleeps/attempts

    out = runner.execute_actions(correlation_id="c2", actions=actions, deadline=deadline)
    assert out["success"] is False
    assert ex.executions <= 2  # should not burn all 5 attempts under tiny budget
