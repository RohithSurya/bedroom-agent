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
            # Local mock doesn't track on/off; assume on when set is called
            return {"entity_id": entity_id, "state": "on", "attributes": attrs}

        return {"entity_id": entity_id, "state": "unknown", "attributes": {}}

    def _verify(self, call: ToolCall, result: ToolResult) -> dict[str, Any]:
        mode = getattr(self.executor, "mode", "active")
        if mode == "shadow":
            return {
                "verified": bool(result.ok),
                "mode": "shadow",
                "note": "state verification skipped",
            }

        if call.tool == "light.set":
            entity_id = str(call.args.get("entity_id", "light.bedroom_lamp"))
            want_pct = int(call.args.get("brightness_pct", 15))

            ent = self._read_entity_state(entity_id)
            attrs = ent.get("attributes", {}) or {}

            got_pct = None

            # HA: attributes.brightness is 0-255
            if "brightness" in attrs and attrs["brightness"] is not None:
                try:
                    got_pct = round((int(attrs["brightness"]) * 100) / 255)
                except Exception:
                    got_pct = None

            # Local mock: may store brightness_pct directly
            if got_pct is None and "brightness_pct" in attrs:
                try:
                    got_pct = int(attrs["brightness_pct"])
                except Exception:
                    got_pct = None

            verified = bool(result.ok) and (got_pct is not None) and (abs(got_pct - want_pct) <= 2)
            return {
                "verified": verified,
                "entity_id": entity_id,
                "want_pct": want_pct,
                "got_pct": got_pct,
                "raw_state": ent.get("state"),
            }

        if call.tool == "switch.set":
            entity_id = str(call.args.get("entity_id", "switch.bedroom_fan_plug"))
            want = str(call.args.get("state", "")).lower()

            ent = self._read_entity_state(entity_id)
            got = str(ent.get("state", "")).lower()

            verified = bool(result.ok) and (got == want)
            return {"verified": verified, "entity_id": entity_id, "want": want, "got": got}

        if call.tool == "tts.say":
            msg = str(call.args.get("message", ""))

            # Local ToolExecutor tracks tts list
            if not hasattr(self.executor, "read_entity_state"):
                state = self.executor.get_state()
                tts = state.get("tts", [])
                verified = bool(result.ok) and (len(tts) > 0) and (tts[-1] == msg)
                return {"verified": verified, "message": msg}

            # Real HA backend: unless you wire TTS to a verifiable entity, treat as best-effort
            return {
                "verified": bool(result.ok),
                "message": msg,
                "note": "no_state_verifier_for_tts_backend",
            }

        return {"verified": bool(result.ok), "note": "no verifier for tool"}

    def execute_actions(
        self,
        *,
        correlation_id: str,
        actions: list[ToolCall],
        cooldown_key: str | None,
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
            print(f"Executing tool: {call.tool} with args {call.args}")  # for visibility in logs
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
