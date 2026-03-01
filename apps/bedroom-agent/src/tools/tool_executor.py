from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from contracts.ha import ToolCall, ToolResult
from core.idempotency import IdempotencyStore
from core.logging_jsonl import JsonlLogger


@dataclass
class ToolExecutor:
    """
    Mock Home Assistant tool executor.
    - ACTIVE: applies side effects to in-memory device_state
    - SHADOW: no side effects
    - Idempotency: caches SUCCESS results by idempotency_key
    - Failure injection: simulate transient tool failures (not cached by default)
    """

    mode: str  # "shadow" or "active"
    logger: JsonlLogger
    idempotency: IdempotencyStore = field(default_factory=IdempotencyStore)

    device_state: Dict[str, Any] = field(
        default_factory=lambda: {
            "lights": {"light.bedroom_lamp": {"brightness_pct": 100}},
            "tts": [],
            "switches": {"switch.bedroom_fan_plug": {"state": "off"}},
        }
    )

    executions: int = 0  # increments only when NOT served from cache

    # tool -> {"remaining": int, "error": str, "cache_failures": bool}
    failure_plan: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def get_state(self) -> dict:
        return self.device_state

    def inject_failure(
        self,
        *,
        tool: str,
        times: int = 1,
        error: str = "simulated_error",
        cache_failures: bool = False,
    ) -> None:
        self.failure_plan[tool] = {
            "remaining": int(times),
            "error": error,
            "cache_failures": bool(cache_failures),
        }

    def _maybe_inject_failure(self, call: ToolCall) -> Optional[ToolResult]:
        plan = self.failure_plan.get(call.tool)
        if not plan:
            return None
        if plan["remaining"] <= 0:
            return None

        plan["remaining"] -= 1
        return ToolResult(
            ok=False,
            tool=call.tool,
            details={
                "cached": False,
                "injected": True,
                "error": plan["error"],
                "remaining_failures": plan["remaining"],
            },
        )

    def execute(self, call: ToolCall) -> ToolResult:
        # If we already succeeded for this idempotency key, return cached success
        cached = self.idempotency.get(call.idempotency_key)
        if cached is not None:
            details = dict(cached.details)
            details["cached"] = True
            details["mode"] = self.mode
            return ToolResult(ok=cached.ok, tool=cached.tool, details=details)

        # Failure injection (transient by default; not cached unless cache_failures=True)
        injected = self._maybe_inject_failure(call)
        if injected is not None:
            self.executions += 1
            cache_failures = bool(self.failure_plan.get(call.tool, {}).get("cache_failures", False))
            if cache_failures:
                self.idempotency.put(call.idempotency_key, injected)
            return injected

        # Not cached + no injected failure -> execute
        self.executions += 1

        if self.mode == "shadow":
            result = ToolResult(
                ok=True,
                tool=call.tool,
                details={"shadow": True, "cached": False, "note": "No side effects in shadow mode"},
            )
            # cache success
            self.idempotency.put(call.idempotency_key, result)
            return result

        # ACTIVE mode: apply side effects
        if call.tool == "light.set":
            entity_id = str(call.args.get("entity_id", "light.bedroom_lamp"))
            state = str(call.args.get("state", "on")).lower()
            if state not in ("on", "off"):
                result = ToolResult(
                    ok=False, tool=call.tool, details={"error": "invalid_state", "state": state}
                )
            else:
                brightness_pct = int(call.args.get("brightness_pct", 15))
                transition_s = float(call.args.get("transition_s", 0))

                self.device_state["lights"].setdefault(entity_id, {})
                self.device_state["lights"][entity_id]["state"] = state
                self.device_state["lights"][entity_id]["transition_s"] = transition_s
                if state == "on":
                    self.device_state["lights"][entity_id]["brightness_pct"] = brightness_pct

                result = ToolResult(
                    ok=True,
                    tool=call.tool,
                    details={
                        "cached": False,
                        "entity_id": entity_id,
                        "state": state,
                        "brightness_pct": brightness_pct if state == "on" else None,
                        "transition_s": transition_s,
                    },
                )

        elif call.tool == "tts.say":
            msg = str(call.args.get("message", ""))
            self.device_state["tts"].append(msg)
            result = ToolResult(ok=True, tool=call.tool, details={"cached": False, "message": msg})

        elif call.tool == "switch.set":
            entity_id = str(call.args.get("entity_id"))
            state = str(call.args.get("state", "off")).lower()
            if state not in ("on", "off"):
                result = ToolResult(
                    ok=False, tool=call.tool, details={"error": "invalid_state", "state": state}
                )
            else:
                self.device_state["switches"].setdefault(entity_id, {})
                self.device_state["switches"][entity_id]["state"] = state
                result = ToolResult(
                    ok=True,
                    tool=call.tool,
                    details={"entity_id": entity_id, "state": state},
                )

        else:
            result = ToolResult(
                ok=False,
                tool=call.tool,
                details={"cached": False, "error": f"unknown_tool:{call.tool}"},
            )

        # cache success only
        if result.ok:
            self.idempotency.put(call.idempotency_key, result)

        return result
