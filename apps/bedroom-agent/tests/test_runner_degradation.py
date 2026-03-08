from __future__ import annotations

from agent.actions import ActionFactory
from agent.orchestrator import Orchestrator
from agent.runner import Runner
from core.logging_jsonl import JsonlLogger
from tools.tool_executor import ToolExecutor
from reliability.retry import RetryPolicy


def test_runner_emits_fallback_tts_on_light_failure(tmp_path):
    logger = JsonlLogger(log_dir=str(tmp_path), tz_name="America/New_York")
    ex = ToolExecutor(mode="active")
    ex.inject_failure(tool="light.set", times=2, error="simulated_timeout")  # hard fail

    orch = Orchestrator()
    out = orch.handle_request(
        intent="night_mode", args={}, state={"presence": True, "guest_mode": False}
    )

    runner = Runner(executor=ex, logger=logger, retry_attempts=1)
    run_out = runner.execute_actions(correlation_id=out["correlation_id"], actions=out["actions"])

    assert run_out["success"] is False
    assert any("couldn't dim the lights" in msg.lower() for msg in ex.device_state["tts"])


def test_runner_verifies_climate_actions(tmp_path):
    logger = JsonlLogger(log_dir=str(tmp_path), tz_name="America/New_York")
    ex = ToolExecutor(mode="active")
    runner = Runner(executor=ex, logger=logger, retry_attempts=0)
    calls = ActionFactory().climate(
        entity_id="climate.bedroom_ac",
        hvac_mode="cool",
        temperature=24,
        fan_mode="auto",
    ).to_tool_calls("cid")

    run_out = runner.execute_actions(
        correlation_id="cid",
        actions=calls,
    )

    assert run_out["success"] is True
    climate = ex.device_state["climate"]["climate.bedroom_ac"]
    assert climate["hvac_mode"] == "cool"
    assert climate["temperature"] == 24
    assert climate["fan_mode"] == "auto"


def test_runner_marks_failure_on_fan_failure(tmp_path):
    logger = JsonlLogger(log_dir=str(tmp_path), tz_name="America/New_York")
    ex = ToolExecutor(mode="active")
    ex.inject_failure(tool="fan.set", times=1, error="simulated_error")  # non-transient-ish

    orch = Orchestrator()
    out = orch.handle_request(
        intent="fan_on", args={}, state={"presence": True, "guest_mode": False}
    )

    runner = Runner(executor=ex, logger=logger)
    run_out = runner.execute_actions(correlation_id=out["correlation_id"], actions=out["actions"])

    assert run_out["success"] is False
    assert any(f["tool"] == "fan.set" for f in run_out["failures"])


def test_runner_retries_transient_fan_failure(tmp_path):
    logger = JsonlLogger(log_dir=str(tmp_path), tz_name="America/New_York")
    ex = ToolExecutor(mode="active")
    ex.inject_failure(tool="fan.set", times=1, error="simulated_timeout")  # transient

    orch = Orchestrator()
    out = orch.handle_request(
        intent="fan_on", args={}, state={"presence": True, "guest_mode": False}
    )

    runner = Runner(
        executor=ex,
        logger=logger,
        tool_retry_policy=RetryPolicy(
            max_attempts=2, base_delay_s=0.01, max_delay_s=0.02, jitter_s=0.0
        ),
    )
    run_out = runner.execute_actions(correlation_id=out["correlation_id"], actions=out["actions"])

    assert run_out["success"] is True
