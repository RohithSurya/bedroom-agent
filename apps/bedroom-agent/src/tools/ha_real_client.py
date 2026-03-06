from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
import requests

from contracts.ha import ToolCall, ToolResult
from core.logging_jsonl import JsonlLogger


@dataclass
class HAToolClientReal:
    base_url: str
    token: str
    logger: JsonlLogger
    mode: str = "active"  # "shadow" or "active"
    timeout_s: float = 5.0
    tts_media_player: str = "media_player.main_bedroom"
    tts_entity_id = "tts.google_translate_en_com"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    def _post_service(self, domain: str, service: str, payload: Dict[str, Any]) -> ToolResult:
        if self.mode == "shadow":
            return ToolResult(
                ok=True, tool=f"{domain}.{service}", details={"shadow": True, "payload": payload}
            )

        try:
            timeout = self.timeout_s if self.timeout_s is not None else 8.0
            r = requests.post(
                self._url(f"/api/services/{domain}/{service}"),
                headers=self._headers(),
                json=payload,
                timeout=timeout,
            )
            ok = 200 <= r.status_code < 300
            return ToolResult(
                ok=ok,
                tool=f"{domain}.{service}",
                details={"status": r.status_code, "body": self._safe_json(r)},
            )
        except requests.RequestException as e:
            return ToolResult(
                ok=False,
                tool=f"{domain}.{service}",
                details={"error": "ha_unreachable", "exc": str(e)},
            )

    @staticmethod
    def _safe_json(r: requests.Response) -> Any:
        try:
            return r.json()
        except Exception:
            return {"text": r.text[:500]}

    def read_entity_state(self, entity_id: str) -> Dict[str, Any]:
        try:
            r = requests.get(
                self._url(f"/api/states/{entity_id}"),
                headers=self._headers(),
                timeout=self.timeout_s,
            )
            if r.status_code == 404:
                return {
                    "entity_id": entity_id,
                    "state": "unknown",
                    "attributes": {},
                    "error": "not_found",
                }
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            return {
                "entity_id": entity_id,
                "state": "unknown",
                "attributes": {},
                "error": "ha_unreachable",
                "exc": str(e),
            }

    def prime_tts(self, message: str) -> None:
        """Pre-cache TTS audio to avoid startup delay on first use."""
        try:
            requests.post(
                self._url("/api/tts_get_url"),
                headers=self._headers(),
                json={
                    "engine_id": self.tts_entity_id,
                    "message": message,
                    "cache": True,
                    "options": {"preferred_format": "mp3", "preferred_sample_rate": 22050},
                },
                timeout=(3.0, 30.0),
            )
        except requests.RequestException as e:
            pass

    def execute(self, call: ToolCall) -> ToolResult:

        if self.mode == "shadow":
            return ToolResult(ok=True, tool="shadow_mode", details={"note": "shadow_mode"})
        tool = call.tool

        # lights-only night mode
        if tool == "light.set":
            entity_id = str(call.args.get("entity_id"))
            payload: Dict[str, Any] = {"entity_id": entity_id}
            state = str(call.args.get("state", "on")).lower()
            if state not in ("on", "off"):
                return ToolResult(
                    ok=False, tool=tool, details={"error": "invalid_state", "state": state}
                )
            if "brightness_pct" in call.args:
                payload["brightness_pct"] = int(call.args["brightness_pct"])
            if "transition_s" in call.args:
                payload["transition"] = float(call.args["transition_s"])
            service = "turn_on" if state == "on" else "turn_off"
            return self._post_service("light", service, payload)

        # fan plug
        if tool == "switch.set":
            entity_id = str(call.args.get("entity_id"))
            state = str(call.args.get("state", "off")).lower()
            if state not in ("on", "off"):
                return ToolResult(
                    ok=False, tool=tool, details={"error": "invalid_state", "state": state}
                )
            svc = "turn_on" if state == "on" else "turn_off"
            return self._post_service("switch", svc, {"entity_id": entity_id})

        if tool == "climate.set_mode":
            entity_id = str(call.args.get("entity_id"))
            hvac_mode = str(call.args.get("hvac_mode", "off")).lower()
            if hvac_mode not in ("off", "cool", "fan_only", "auto"):
                return ToolResult(
                    ok=False,
                    tool=tool,
                    details={"error": "invalid_hvac_mode", "hvac_mode": hvac_mode},
                )
            return self._post_service(
                "climate",
                "set_hvac_mode",
                {"entity_id": entity_id, "hvac_mode": hvac_mode},
            )

        if tool == "climate.set_temperature":
            entity_id = str(call.args.get("entity_id"))
            try:
                temperature = int(call.args.get("temperature"))
            except Exception:
                return ToolResult(
                    ok=False,
                    tool=tool,
                    details={
                        "error": "invalid_temperature",
                        "temperature": call.args.get("temperature"),
                    },
                )
            return self._post_service(
                "climate",
                "set_temperature",
                {"entity_id": entity_id, "temperature": temperature},
            )

        if tool == "climate.set_fan_mode":
            entity_id = str(call.args.get("entity_id"))
            fan_mode = str(call.args.get("fan_mode", "auto")).lower()
            if fan_mode not in ("auto", "low", "medium", "high"):
                return ToolResult(
                    ok=False, tool=tool, details={"error": "invalid_fan_mode", "fan_mode": fan_mode}
                )
            return self._post_service(
                "climate",
                "set_fan_mode",
                {"entity_id": entity_id, "fan_mode": fan_mode},
            )

        # temp placeholder for "speech" until you wire HomePod TTS
        if tool == "tts.say":
            msg = str(call.args.get("message", ""))
            return self._post_service(
                "script",
                "turn_on",
                {
                    "entity_id": "script.bedroom_agent_speak",
                    "variables": {"message": msg, "volume_level": 0.5},
                },
            )

        return ToolResult(ok=False, tool=tool, details={"error": "unknown_tool"})
