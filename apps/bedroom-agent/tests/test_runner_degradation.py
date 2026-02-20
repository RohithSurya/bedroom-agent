from __future__ import annotations

from agent.orchestrator import Orchestrator
from agent.runner import Runner
from core.logging_jsonl import JsonlLogger
from tools.tool_executor import ToolExecutor


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
