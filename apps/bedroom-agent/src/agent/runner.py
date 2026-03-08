from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import random
import time
from agent.tool_behaviors import ToolBehaviorRegistry
from reliability.retry import RetryPolicy

from contracts.ha import ToolCall, ToolResult
from core.cooldowns import CooldownStore
from core.ids import new_idempotency_key
from core.logging_jsonl import JsonlLogger
from tools.tool_executor import ToolExecutor
from reliability.circuit_breaker import CircuitBreaker
from reliability.deadline import Deadline


@dataclass
class Runner:
    executor: ToolExecutor
    cooldowns: CooldownStore = field(default_factory=CooldownStore)
    logger: JsonlLogger = field(
        default_factory=lambda: JsonlLogger(
            log_dir="/tmp/bedroom-agent-runner",
            tz_name="America/New_York",
        )
    )
    retry_attempts: int = 1  # v0 default
    tool_retry_policy: RetryPolicy = field(default_factory=lambda: RetryPolicy(max_attempts=1))
    tool_timeout_s: float = 8.0  # extra safety, especially for HTTP/HA
    verification_settle_attempts: int = 5
    verification_settle_delay_s: float = 0.2
    behavior_registry: ToolBehaviorRegistry = field(default_factory=ToolBehaviorRegistry)
    ha_breaker: CircuitBreaker = field(
        default_factory=lambda: CircuitBreaker(failure_threshold=3, recovery_timeout_s=10.0)
    )

    def read_entity_state(self, entity_id: str) -> dict[str, Any]:
        return self._read_entity_state(entity_id)

    def _behavior_for(self, call: ToolCall):
        return self.behavior_registry.for_call(call)

    def _is_retryable_tool(self, call: ToolCall) -> bool:
        return self._behavior_for(call).is_retryable(call)

    def _log_breaker_transition(
        self, correlation_id: str, before: str, after: str, *, tool: str
    ) -> None:
        if before != after:
            self.logger.write(
                correlation_id=correlation_id,
                event_type="breaker_transition",
                payload={"tool": tool, "before": before, "after": after},
            )

    def _is_transient_failure(self, result: ToolResult) -> bool:
        d = result.details or {}
        err = str(d.get("error", "")).lower()
        status = d.get("status")

        if status in (502, 503, 504):
            return True
        if "timeout" in err or "unreachable" in err:
            return True
        if err in ("ha_unreachable", "simulated_timeout", "simulated_error"):
            return True
        return False

    def _execute_with_transient_retries(
        self, call: ToolCall, deadline: Deadline | None = None
    ) -> ToolResult:
        if deadline is not None and deadline.expired():
            return ToolResult(ok=False, tool=call.tool, details={"error": "deadline_exceeded"})
        policy = self.tool_retry_policy
        attempts = max(1, int(policy.max_attempts))

        last: ToolResult | None = None

        if self._is_retryable_tool(call) and (not self.ha_breaker.allow()):
            return ToolResult(
                ok=False,
                tool=call.tool,
                details={"error": "circuit_open", "breaker_state": self.ha_breaker.state()},
            )
        for attempt in range(1, attempts + 1):
            try:
                # executor.execute usually doesn't raise, but HTTP libs sometimes do
                timeout = self.tool_timeout_s
                if deadline is not None:
                    # keep a tiny safety margin so we can still return/log cleanly
                    timeout = max(0.2, min(timeout, deadline.remaining() - 0.05))

                call2 = call.model_copy(update={"timeout_s": timeout})
                res = self.executor.execute(call2)
                before = self.ha_breaker.state()

                if res.ok:
                    self.ha_breaker.record_success()
                else:
                    if self._is_transient_failure(res):
                        self.ha_breaker.record_failure()
                    else:
                        # HA responded; not an outage-type failure
                        self.ha_breaker.record_success()

                after = self.ha_breaker.state()
                self._log_breaker_transition(call.correlation_id, before, after, tool=call.tool)
            except Exception as e:  # noqa: BLE001
                res = ToolResult(
                    ok=False, tool=call.tool, details={"error": "tool_exception", "exc": str(e)}
                )

            last = res
            if res.ok:
                return res

            # If not ok: decide whether to retry
            if (not self._is_retryable_tool(call)) or (not self._is_transient_failure(res)):
                return res

            # sleep before next attempt (exponential backoff + jitter)
            if attempt < attempts:
                delay = min(policy.base_delay_s * (2 ** (attempt - 1)), policy.max_delay_s)
                delay += random.uniform(0, policy.jitter_s)

                if deadline is not None:
                    delay = min(delay, max(0.0, deadline.remaining() - 0.05))

                if delay <= 0.0:
                    return res
                time.sleep(delay)

        assert last is not None
        return last

    def _read_entity_state(self, entity_id: str) -> dict[str, Any]:
        """
        Returns an HA-like entity state dict:
        - HA backend: real /api/states/<entity_id> shape (state + attributes)
        - local backend: synthesized to match the same interface
        """
        # Real HA backend
        if hasattr(self.executor, "read_entity_state"):
            return self.executor.read_entity_state(entity_id)

        # Local backend (ToolExecutor)
        s = self.executor.get_state()

        if entity_id.startswith("fan."):
            return {
                "entity_id": entity_id,
                "state": s.get("fans", {}).get(entity_id, {}).get("state", "unknown"),
                "attributes": {},
            }

        if entity_id.startswith("switch."):
            return {
                "entity_id": entity_id,
                "state": s.get("switches", {}).get(entity_id, {}).get("state", "unknown"),
                "attributes": {},
            }

        if entity_id.startswith("light."):
            attrs = dict(s.get("lights", {}).get(entity_id, {}))
            # keep any local brightness_pct
            # also provide HA-style brightness (0-255) if we can
            if "brightness" not in attrs and "brightness_pct" in attrs:
                try:
                    attrs["brightness"] = round(int(attrs["brightness_pct"]) * 255 / 100)
                except Exception:
                    pass
            return {
                "entity_id": entity_id,
                "state": attrs.get("state", "unknown"),
                "attributes": attrs,
            }

        if entity_id.startswith("climate."):
            attrs = dict(s.get("climate", {}).get(entity_id, {}))
            return {
                "entity_id": entity_id,
                "state": attrs.get("state", attrs.get("hvac_mode", "unknown")),
                "attributes": attrs,
            }

        return {"entity_id": entity_id, "state": "unknown", "attributes": {}}

    def _verify(self, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        return self._behavior_for(call).verify(self, call, result)

    def _settle_verification(
        self,
        call: ToolCall,
        result: ToolResult,
        verify: dict[str, Any],
        *,
        behavior,
        deadline: Deadline | None = None,
    ) -> dict[str, Any]:
        if verify.get("verified", False) or (not result.ok) or (not behavior.is_verification_critical(call)):
            return verify

        attempts = max(0, int(self.verification_settle_attempts))
        settled = verify

        for _ in range(attempts):
            delay = float(self.verification_settle_delay_s)
            if deadline is not None:
                delay = min(delay, max(0.0, deadline.remaining() - 0.01))
            if delay <= 0.0:
                break
            time.sleep(delay)
            settled = self._verify(call, result)
            if settled.get("verified", False):
                break

        return settled

    def execute_actions(
        self,
        *,
        correlation_id: str,
        actions: list[ToolCall],
        cooldown_key: str | None = None,
        cooldown_seconds: int = 0,
        deadline: Deadline | None = None,
    ) -> dict[str, Any]:
        if deadline is not None and deadline.expired():
            return {
                "success": False,
                "failures": [{"reason": "deadline_exceeded", "details": {"where": "runner_start"}}],
            }
        failures: list[dict[str, Any]] = []
        executed_tools: list[str] = []
        light_ok = True

        for call in actions:
            behavior = self._behavior_for(call)

            # Suppress the plan's normal spoken confirmation if lights already failed.
            if call.tool == "tts.say":
                if not light_ok:
                    continue

            # Execute + log
            print(f"Executing tool: {call.tool} with args {call.args}")  # for visibility in logs
            result = self._execute_with_transient_retries(call, deadline)
            executed_tools.append(call.tool)
            self.logger.write(
                correlation_id=correlation_id, event_type="tool_result", payload=result.model_dump()
            )

            # Verify + log
            verify = self._verify(call, result)
            verify = self._settle_verification(
                call,
                result,
                verify,
                behavior=behavior,
                deadline=deadline,
            )
            self.logger.write(
                correlation_id=correlation_id,
                event_type="verification",
                payload={"tool": call.tool, "verify": verify},
            )

            # Generic failure tracking: if an actuator call failed, it's a failure.
            # (Keep tts.say best-effort so you still get an explanation even when HA is flaky.)
            if (call.tool != "tts.say") and (not result.ok):
                failures.append(
                    {
                        "tool": call.tool,
                        "reason": "tool_failed",
                        "details": {"result": result.details},
                    }
                )

            if behavior.is_verification_critical(call):
                attempts_left = self.retry_attempts
                while attempts_left > 0 and (not verify.get("verified", False)):
                    attempts_left -= 1
                    r2 = self.executor.execute(call)  # SAME idempotency key
                    self.logger.write(
                        correlation_id=correlation_id,
                        event_type="tool_result_retry",
                        payload=r2.model_dump(),
                    )
                    v2 = self._verify(call, r2)
                    v2 = self._settle_verification(
                        call,
                        r2,
                        v2,
                        behavior=behavior,
                        deadline=deadline,
                    )
                    self.logger.write(
                        correlation_id=correlation_id,
                        event_type="verification_retry",
                        payload={"tool": call.tool, "verify": v2},
                    )

                    result, verify = r2, v2

                if not verify.get("verified", False):
                    light_ok = False
                    failures.append(
                        {
                            "tool": call.tool,
                            "reason": "light_verify_failed",
                            "details": {"verify": verify, "result": result.details},
                        }
                    )

        # Graceful degradation: if lights failed, speak fallback message.
        if not light_ok:
            fallback = ToolCall(
                tool="tts.say",
                args={"message": "I couldn't change the lights right now."},
                idempotency_key=new_idempotency_key(),
                correlation_id=correlation_id,
            )
            fr = self.executor.execute(fallback)
            self.logger.write(
                correlation_id=correlation_id, event_type="tool_result", payload=fr.model_dump()
            )
            fv = self._verify(fallback, fr)
            self.logger.write(
                correlation_id=correlation_id,
                event_type="verification",
                payload={"tool": "tts.say", "verify": fv},
            )
            executed_tools.append("tts.say")

        success = len(failures) == 0

        mode = getattr(self.executor, "mode", "active")
        if success and mode == "active" and cooldown_key and cooldown_seconds > 0:
            self.cooldowns.mark_ran(cooldown_key, cooldown_seconds)
            self.logger.write(
                correlation_id=correlation_id,
                event_type="cooldown_marked",
                payload={"key": cooldown_key, "seconds": cooldown_seconds},
            )
        self.logger.write(
            correlation_id=correlation_id,
            event_type="final_outcome",
            payload={
                "success": success,
                "failures": failures,
                "executed_tools": executed_tools,
                "mode": self.executor.mode,
            },
        )
        return {"success": success, "failures": failures, "executed_tools": executed_tools}
