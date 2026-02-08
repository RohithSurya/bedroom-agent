from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from agent.orchestrator import Orchestrator  # noqa: E402
from agent.runner import Runner  # noqa: E402
from core.config import Settings  # noqa: E402
from core.logging_jsonl import JsonlLogger  # noqa: E402
from tools.tool_executor import ToolExecutor  # noqa: E402


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_scenario(path: Path) -> int:
    cfg = Settings()
    logger = JsonlLogger(log_dir=cfg.LOG_DIR, tz_name=cfg.TIMEZONE)

    orch = Orchestrator()
    executor = ToolExecutor(mode=cfg.AGENT_MODE)
    runner = Runner(executor=executor, logger=logger, retry_attempts=1)

    scenario = _load_yaml(path)
    state = dict(scenario.get("initial_state", {}))

    print(f"Scenario: {scenario.get('name')} — {scenario.get('description', '')}")
    steps = scenario.get("steps", [])
    failures = 0

    for idx, step in enumerate(steps, start=1):
        req = step.get("request", {})
        exp = step.get("expect", {})
        intent = req.get("intent", "")
        args = req.get("args", {})

        # Optional failure injection for this step
        for inj in step.get("failure_injection", []) or []:
            executor.inject_failure(
                tool=str(inj.get("tool")),
                times=int(inj.get("times", 1)),
                error=str(inj.get("error", "simulated_error")),
                cache_failures=bool(inj.get("cache_failures", False)),
            )

        out = orch.handle_request(intent=intent, args=args, state=state)
        cid = out["correlation_id"]
        decision = out["decision"]
        actions = out["actions"]

        logger.write(
            correlation_id=cid,
            event_type="request",
            payload={"intent": intent, "args": args, "state": state},
        )
        logger.write(
            correlation_id=cid, event_type="policy_decision", payload=decision.model_dump()
        )
        logger.write(
            correlation_id=cid,
            event_type="actions_planned",
            payload={"actions": [a.model_dump() for a in actions]},
        )

        run_out = runner.execute_actions(correlation_id=cid, actions=actions)

        # Assertions
        want_decision = exp.get("decision")
        if want_decision and decision.decision != want_decision:
            failures += 1
            print(
                f"  ❌ Step {idx}: decision mismatch: got={decision.decision} want={want_decision}"
            )

        want_tools = exp.get("action_tools", [])
        got_tools = [a.tool for a in actions]
        for t in want_tools:
            if t not in got_tools:
                failures += 1
                print(f"  ❌ Step {idx}: missing expected tool: {t}")

        if "final_success" in exp and bool(run_out["success"]) != bool(exp["final_success"]):
            failures += 1
            print(
                f"  ❌ Step {idx}: final_success mismatch: got={run_out['success']} want={exp['final_success']}"
            )

        if failures == 0:
            print(
                f"  ✅ Step {idx}: ok (mode={cfg.AGENT_MODE}, decision={decision.decision}, final_success={run_out['success']})"
            )

    print(f"Result: {'PASS' if failures == 0 else 'FAIL'} (failures={failures})")
    print(f"Logs: {cfg.LOG_DIR}/events.jsonl")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    scenario_path = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path("evals/scenarios/night_mode.yaml")
    )
    raise SystemExit(run_scenario(scenario_path))
