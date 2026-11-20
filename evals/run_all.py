"""Run every eval scenario and print a summary table.

Usage (from the repo root):
    TOOL_BACKEND=local AGENT_MODE=active python evals/run_all.py

Each scenario runs in its own process so cooldowns and failure injections never leak
between scenarios. Exits non-zero if any scenario fails, so CI can gate on it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIOS = sorted((HERE / "scenarios").glob("*.yaml"))


def main() -> int:
    env = {**os.environ, "TOOL_BACKEND": "local", "AGENT_MODE": os.environ.get("AGENT_MODE", "active")}
    rows: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as logs:
        env["LOG_DIR"] = logs
        for path in SCENARIOS:
            proc = subprocess.run(
                [sys.executable, str(HERE / "harness.py"), str(path)],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )
            ok = proc.returncode == 0
            detail = "" if ok else (proc.stdout + proc.stderr).strip().splitlines()[-1][:100]
            rows.append((path.stem, ok, detail))

    width = max(len(name) for name, _, _ in rows)
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    passed = sum(ok for _, ok, _ in rows)
    print(f"\n{passed}/{len(rows)} scenarios passed")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
