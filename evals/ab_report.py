from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Make `src/` importable
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))

from agent.orchestrator import Orchestrator  # noqa: E402
from core.config import Settings  # noqa: E402
from core.logging_jsonl import JsonlLogger  # noqa: E402


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _normalize_actions(actions: list[Any]) -> list[dict[str, Any]]:
    """
    Compare tools by tool name + args only (ignore random id keys).
    """
    out: list[dict[str, Any]] = []
    for a in actions:
        # ToolCall is a pydantic model
        d = a.model_dump()
        out.append({"tool": d.get("tool"), "args": d.get("args", {})})
    return out


@dataclass
class StepResult:
    step_idx: int
    now_s: int
    intent: str
    decision: str
    reason: str
    cooldown_seconds: int
    actions: list[dict[str, Any]]
    committed_cooldowns: dict[str, int]


def run_mode(scenario: dict[str, Any], mode: str, logger: JsonlLogger) -> list[StepResult]:
    """
    mode = 'shadow' or 'active'
    Shadow: does not commit effects to state
    Active: commits cooldown updates to state
    """
    orch = Orchestrator()

    state = dict(scenario.get("initial_state", {}))
    state.setdefault("now_s", 0)
    state.setdefault("cooldowns", {})

    results: list[StepResult] = []

    for idx, step in enumerate(scenario.get("steps", []), start=1):
        advance = int(step.get("advance_seconds", 0))
        state["now_s"] = int(state["now_s"]) + advance

        req = step.get("request", {})
        intent = req.get("intent", "")
        args = req.get("args", {})

        out = orch.handle_request(intent=intent, args=args, state=state)
        cid = out["correlation_id"]
        decision_obj = out["decision"]
        actions_obj = out["actions"]

        # log
        logger.write(
            correlation_id=cid,
            event_type="ab_request",
            payload={
                "mode": mode,
                "step": idx,
                "now_s": state["now_s"],
                "intent": intent,
                "args": args,
                "state": state,
            },
        )
        logger.write(
            correlation_id=cid,
            event_type="ab_decision",
            payload={"mode": mode, **decision_obj.model_dump()},
        )
        logger.write(
            correlation_id=cid,
            event_type="ab_actions_planned",
            payload={"mode": mode, "actions": [a.model_dump() for a in actions_obj]},
        )

        # commit effects only in ACTIVE
        committed: dict[str, int] = {}
        if (
            mode == "active"
            and decision_obj.decision == "allow"
            and decision_obj.cooldown_seconds > 0
        ):
            until = int(state["now_s"]) + int(decision_obj.cooldown_seconds)
            # store cooldown per intent
            state["cooldowns"][f"{intent}_until"] = until
            committed[f"{intent}_until"] = until

        results.append(
            StepResult(
                step_idx=idx,
                now_s=int(state["now_s"]),
                intent=intent,
                decision=decision_obj.decision,
                reason=decision_obj.reason,
                cooldown_seconds=int(decision_obj.cooldown_seconds),
                actions=_normalize_actions(actions_obj),
                committed_cooldowns=committed,
            )
        )

    return results


def diff_report(shadow: list[StepResult], active: list[StepResult]) -> dict[str, Any]:
    steps = min(len(shadow), len(active))
    mismatches: list[dict[str, Any]] = []

    for i in range(steps):
        s = shadow[i]
        a = active[i]

        step_mismatch: dict[str, Any] = {
            "step": i + 1,
            "intent": s.intent,
            "now_s": s.now_s,
            "diffs": [],
        }

        if s.decision != a.decision:
            step_mismatch["diffs"].append(
                {
                    "type": "decision",
                    "shadow": {"decision": s.decision, "reason": s.reason},
                    "active": {"decision": a.decision, "reason": a.reason},
                }
            )

        if s.actions != a.actions:
            step_mismatch["diffs"].append(
                {"type": "actions", "shadow": s.actions, "active": a.actions}
            )

        if s.committed_cooldowns != a.committed_cooldowns:
            step_mismatch["diffs"].append(
                {
                    "type": "commits",
                    "shadow": s.committed_cooldowns,
                    "active": a.committed_cooldowns,
                }
            )

        if step_mismatch["diffs"]:
            mismatches.append(step_mismatch)

    return {
        "steps_compared": steps,
        "mismatch_steps": len(mismatches),
        "mismatches": mismatches,
    }


def main() -> int:
    scenario_path = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path("evals/scenarios/night_mode_ab.yaml")
    )
    scenario = _load_yaml(scenario_path)

    cfg = Settings()
    logger = JsonlLogger(log_dir=cfg.LOG_DIR, tz_name=cfg.TIMEZONE)

    shadow = run_mode(scenario, "shadow", logger)
    active = run_mode(scenario, "active", logger)

    report = diff_report(shadow, active)

    print("\n=== Shadow vs Active A/B Report ===")
    print(f"Scenario: {scenario.get('name')} — {scenario.get('description', '')}")
    print(f"Steps compared: {report['steps_compared']}")
    print(f"Mismatch steps: {report['mismatch_steps']}\n")

    if report["mismatch_steps"] == 0:
        print("✅ No differences (shadow and active behave the same for this scenario).")
    else:
        for m in report["mismatches"]:
            print(f"Step {m['step']} @ t={m['now_s']}s intent={m['intent']}")
            for d in m["diffs"]:
                if d["type"] == "decision":
                    print(f"  - DECISION mismatch:")
                    print(f"    shadow: {d['shadow']['decision']} ({d['shadow']['reason']})")
                    print(f"    active: {d['active']['decision']} ({d['active']['reason']})")
                elif d["type"] == "actions":
                    print(f"  - ACTIONS mismatch:")
                    print(f"    shadow: {d['shadow']}")
                    print(f"    active: {d['active']}")
                elif d["type"] == "commits":
                    print(f"  - COMMITS mismatch:")
                    print(f"    shadow: {d['shadow']}")
                    print(f"    active: {d['active']}")
            print()

    # Save report JSON
    out_path = Path(cfg.LOG_DIR) / "ab_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved: {out_path}")
    print(f"Logs: {cfg.LOG_DIR}/events.jsonl\n")

    return 0 if report["mismatch_steps"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
