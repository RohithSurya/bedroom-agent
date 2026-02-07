from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

# Make `src/` importable without messing with env vars
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from agent.orchestrator import Orchestrator  # noqa: E402
from core.config import Settings  # noqa: E402
from core.logging_jsonl import JsonlLogger  # noqa: E402


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_scenario(path: Path) -> int:
    cfg = Settings()
    logger = JsonlLogger(log_dir=cfg.LOG_DIR, tz_name=cfg.TIMEZONE)
    orch = Orchestrator()

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
            correlation_id=cid,
            event_type="policy_decision",
            payload=decision.model_dump(),
        )
        logger.write(
            correlation_id=cid,
            event_type="actions_planned",
            payload={"actions": [a.model_dump() for a in actions]},
        )

        got_decision = decision.decision
        want_decision = exp.get("decision")
        if want_decision and got_decision != want_decision:
            failures += 1
            print(f"  ❌ Step {idx}: decision mismatch: got={got_decision} want={want_decision}")

        want_tools = exp.get("action_tools", [])
        got_tools = [a.tool for a in actions]
        for t in want_tools:
            if t not in got_tools:
                failures += 1
                print(f"  ❌ Step {idx}: missing expected tool: {t}")

        if failures == 0:
            print(f"  ✅ Step {idx}: ok (decision={got_decision}, tools={got_tools})")

    print(f"Result: {'PASS' if failures == 0 else 'FAIL'} (failures={failures})")
    print(f"Logs: {cfg.LOG_DIR}/events.jsonl")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    scenario_path = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path("evals/scenarios/night_mode.yaml")
    )
    raise SystemExit(run_scenario(scenario_path))
