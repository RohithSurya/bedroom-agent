from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contracts.ha import ToolCall, ToolResult
from core.cooldowns import CooldownStore
from core.ids import new_idempotency_key
from core.logging_jsonl import JsonlLogger
from tools.tool_executor import ToolExecutor


@dataclass
class Runner:
    executor: ToolExecutor
    cooldowns: CooldownStore
    logger: JsonlLogger
    retry_attempts: int = 1  # v0 default

    def _verify(self, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        # In shadow mode, we can only verify "the call succeeded" logically
        if self.executor.mode == "shadow":
            return {
                "verified": bool(result.ok),
                "mode": "shadow",
                "note": "state verification skipped",
            }

        if call.tool == "light.set":
            state = self.executor.get_state()
            entity_id = str(call.args.get("entity_id", "light.bedroom_lamp"))
            want = int(call.args.get("brightness_pct", 15))
            got = int(state.get("lights", {}).get(entity_id, {}).get("brightness_pct", -1))
            verified = bool(result.ok) and (got == want)
            return {"verified": verified, "entity_id": entity_id, "want": want, "got": got}

        if call.tool == "tts.say":
            state = self.executor.get_state()
            msg = str(call.args.get("message", ""))
            tts = state.get("tts", [])
            verified = bool(result.ok) and (len(tts) > 0) and (tts[-1] == msg)
            return {"verified": verified, "message": msg}

        return {"verified": bool(result.ok), "note": "no verifier for tool"}

    def execute_actions(
        self,
        *,
        correlation_id: str,
        actions: list[ToolCall],
        cooldown_key: str,
        cooldown_seconds: int,
    ) -> dict[str, Any]:
        failures: list[dict[str, Any]] = []
        executed_tools: list[str] = []
        light_ok = True

        for call in actions:
            # Skip "success" TTS if lights already failed (we'll speak fallback later)
            if call.tool == "tts.say":
                msg = str(call.args.get("message", ""))
                if ("Night mode on" in msg or "Lights dimmed" in msg) and (not light_ok):
                    continue

            # Execute + log
            result = self.executor.execute(call)
            executed_tools.append(call.tool)
            self.logger.write(
                correlation_id=correlation_id, event_type="tool_result", payload=result.model_dump()
            )

            # Verify + log
            verify = self._verify(call, result)
            self.logger.write(
                correlation_id=correlation_id,
                event_type="verification",
                payload={"tool": call.tool, "verify": verify},
            )

            # Retry-on-failure (only for light.set in v0)
            if call.tool == "light.set":
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
                            "tool": "light.set",
                            "reason": "light_verify_failed",
                            "details": {"verify": verify, "result": result.details},
                        }
                    )

        # Graceful degradation: if lights failed, speak fallback message
        if not light_ok:
            fallback = ToolCall(
                tool="tts.say",
                args={"message": "Night mode: I couldn't dim the lights right now."},
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
