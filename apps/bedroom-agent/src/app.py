from __future__ import annotations

from contextlib import asynccontextmanager
import time
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.decision_engine import DecisionEngine
from agent.mqtt_listener import Z2MMqttListener
from agent.status_service import StatusService
from memory.sqlite_kv import SqliteKV
from agent.orchestrator import Orchestrator
from agent.nl_router import NLRouter
from agent.runner import Runner
from contracts.ha import ToolCall
from core.config import Settings
from core.cooldowns import CooldownStore
from core.ids import new_correlation_id, new_idempotency_key
from core.logging_jsonl import JsonlLogger
from llm.ollama_client import OllamaClient
from tools.ha_http_client import HAToolClientHTTP
from tools.tool_executor import ToolExecutor
from tools.ha_real_client import HAToolClientReal
from vision.image_source import BedroomImageSource
from vision.room_analyzer import BedroomRoomAnalyzer


class AgentRunRequest(BaseModel):
    intent: Literal[
        "night_mode",
        "fan_on",
        "fan_off",
        "enter_room",
        "sleep_mode",
        "focus_start",
        "focus_end",
        "comfort_adjust",
        "no_action",
    ]
    args: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)


class AgentChatRequest(BaseModel):
    """Natural language entrypoint.

    This is what you'll call from:
    - Home Assistant webhook (Siri -> scene -> webhook)
    - a simple web chat UI

    Keep it minimal for hackathon reliability.
    """

    text: str
    state: dict[str, Any] = Field(default_factory=dict)


class AgentAppState:
    def __init__(self, settings: Settings) -> None:
        cooldowns = CooldownStore()
        self.settings = settings
        self.cooldowns = cooldowns
        self.logger = JsonlLogger(log_dir=settings.LOG_DIR, tz_name=settings.TIMEZONE)

        # Optional LLM backend (local on-device). If it isn't running, routing falls back safely.
        self.llm = OllamaClient(
            base_url=settings.LLM_BASE_URL,
            model=settings.LLM_MODEL,
            timeout_s=float(settings.LLM_TIMEOUT_S),
        )
        self.decision_llm = (
            OllamaClient(
                base_url=settings.LLM_BASE_URL,
                model=settings.LLM_MODEL,
                timeout_s=float(settings.LLM_DECISION_TIMEOUT_S),
            )
            if bool(settings.LLM_DECISION_ENABLED)
            else None
        )
        self.router = NLRouter(llm=self.llm)
        self.kv = SqliteKV(settings.SQLITE_PATH)
        self.status_service = StatusService(kv=self.kv, llm=self.llm, tz_name=settings.TIMEZONE)
        self.room_analyzer = BedroomRoomAnalyzer(
            kv=self.kv,
            llm=self.llm,
            image_source=BedroomImageSource(
                base_url=settings.HA_BASE_URL,
                token=settings.HA_TOKEN,
                camera_mode=settings.CAMERA_MODE,
                camera_entity_id=settings.CAMERA_ENTITY_ID,
                camera_device=settings.CAMERA_DEVICE,
                camera_width=int(settings.CAMERA_WIDTH),
                camera_height=int(settings.CAMERA_HEIGHT),
                camera_skip_frames=int(settings.CAMERA_SKIP_FRAMES),
                fallback_image_path=settings.VISION_FALLBACK_IMAGE_PATH,
                debug_save_dir=settings.VISION_DEBUG_SAVE_DIR,
            ),
            enabled=bool(settings.VISION_ANALYSIS_ENABLED),
            prompt_profile=settings.VISION_PROMPT_PROFILE,
            max_output_tokens=int(settings.VISION_MAX_OUTPUT_TOKENS),
        )
        self.decision_engine = DecisionEngine(
            kv=self.kv,
            llm=self.decision_llm,
            max_events=int(settings.LLM_DECISION_MAX_EVENTS),
            min_confidence=float(settings.LLM_DECISION_MIN_CONFIDENCE),
            use_vision=bool(settings.LLM_DECISION_USE_VISION),
        )

        self.orchestrator = Orchestrator(cooldowns=cooldowns)
        self.runner = Runner(
            executor=_build_executor(settings, logger=self.logger),
            cooldowns=cooldowns,
            logger=self.logger,
        )

        self.mqtt = Z2MMqttListener(
            mqtt_host=settings.MQTT_HOST,
            mqtt_port=int(settings.MQTT_PORT),
            mqtt_username=settings.MQTT_USERNAME,
            mqtt_password=settings.MQTT_PASSWORD,
            door_topic=settings.Z2M_DOOR_TOPIC,
            presence_topic=settings.Z2M_PRESENCE_TOPIC,
            tz_name=settings.TIMEZONE,
            quiet_start=settings.QUIET_HOURS_START,
            quiet_end=settings.QUIET_HOURS_END,
            entry_window_s=int(settings.ENTRY_WINDOW_S),
            entry_cooldown_s=int(settings.ENTRY_COOLDOWN_S),
            vacancy_off_delay_s=int(settings.VACANCY_OFF_DELAY_S),
            kv=self.kv,
            on_enter=self._on_enter_room,
            on_vacant=self._on_room_vacant,
        )

    @staticmethod
    def _coerce_float_state(raw: dict[str, Any]) -> float | None:
        try:
            return float(raw.get("state"))
        except Exception:
            return None

    def _build_vision_state(self, max_age_s: int = 120) -> dict[str, Any]:
        latest = self.kv.get("vision", "latest_bedroom_analysis", None)
        if not isinstance(latest, dict):
            return {"available": False}

        captured_at_ms = latest.get("captured_at_ms")
        age_s = None
        if isinstance(captured_at_ms, (int, float)):
            age_s = max(0.0, time.time() - (float(captured_at_ms) / 1000.0))
        if age_s is not None and age_s > max_age_s:
            return {"available": False, "age_s": round(age_s, 1)}

        return {
            "available": True,
            "occupied": latest.get("occupied"),
            "bed_state": latest.get("bed_state"),
            "desk_state": latest.get("desk_state"),
            "focus_readiness": latest.get("focus_readiness"),
            "sleep_readiness": latest.get("sleep_readiness"),
            "age_s": round(age_s, 1) if age_s is not None else None,
        }

    def build_runtime_state(self, extra_state: dict[str, Any] | None = None) -> dict[str, Any]:
        beliefs = self.kv.get_namespace("belief")
        prefs = self.kv.get_namespace("prefs")

        light_entity_id = self.settings.ENTRY_LIGHT_ENTITY_ID
        fan_entity_id = self.settings.BEDROOM_FAN_ENTITY_ID
        ac_entity_id = self.settings.BEDROOM_AC_ENTITY_ID
        temp_entity_id = self.settings.TEMP_SENSOR_ENTITY_ID
        humidity_entity_id = self.settings.HUMIDITY_SENSOR_ENTITY_ID

        light_raw = self.runner.read_entity_state(light_entity_id) if light_entity_id else {}
        fan_raw = self.runner.read_entity_state(fan_entity_id) if fan_entity_id else {}
        ac_raw = self.runner.read_entity_state(ac_entity_id) if ac_entity_id else {}
        temp_raw = self.runner.read_entity_state(temp_entity_id) if temp_entity_id else {}
        humidity_raw = self.runner.read_entity_state(humidity_entity_id) if humidity_entity_id else {}

        temperature_c = self._coerce_float_state(temp_raw)
        humidity_pct = self._coerce_float_state(humidity_raw)
        ac_attrs = ac_raw.get("attributes", {}) if isinstance(ac_raw, dict) else {}
        ac_available = bool(ac_entity_id) and not str(ac_raw.get("error", "")).strip()

        state = {
            "presence": bool(beliefs.get("presence", False)),
            "door_open": bool(beliefs.get("door_open", False)),
            "guest_mode": bool(prefs.get("guest_mode", False)),
            "temperature_entity_id": temp_entity_id,
            "humidity_entity_id": humidity_entity_id,
            "temperature_c": temperature_c,
            "humidity_pct": humidity_pct,
            "light_entity_id": light_entity_id,
            "light_state": str(light_raw.get("state", "unknown")).lower(),
            "fan_entity_id": fan_entity_id,
            "fan_state": str(fan_raw.get("state", "unknown")).lower(),
            "ac_entity_id": ac_entity_id,
            "ac_available": ac_available,
            "ac_state": str(ac_raw.get("state", "unknown")).lower(),
            "ac_hvac_mode": str(ac_attrs.get("hvac_mode", ac_raw.get("state", "unknown"))).lower(),
            "ac_target_temp_c": ac_attrs.get("temperature"),
            "ac_fan_mode": str(ac_attrs.get("fan_mode", "")).lower() or None,
            "comfort_trigger_temp_c": float(self.settings.COMFORT_TRIGGER_TEMP_C),
            "comfort_trigger_humidity_pct": float(self.settings.COMFORT_TRIGGER_HUMIDITY_PCT),
            "comfort_target_temp_c": int(self.settings.COMFORT_TARGET_TEMP_C),
            "sleep_target_temp_c": int(self.settings.SLEEP_TARGET_TEMP_C),
            "focus_mode_enable_fan": bool(self.settings.FOCUS_MODE_ENABLE_FAN),
            "focus_mode_enable_climate": bool(self.settings.FOCUS_MODE_ENABLE_CLIMATE),
            "sleep_mode_enable_climate": bool(self.settings.SLEEP_MODE_ENABLE_CLIMATE),
            "comfort_use_fan_fallback": bool(self.settings.COMFORT_USE_FAN_FALLBACK),
            "vision": self._build_vision_state(),
        }
        state["room_uncomfortable"] = bool(
            (temperature_c is not None and temperature_c >= float(self.settings.COMFORT_TRIGGER_TEMP_C))
            or (
                humidity_pct is not None
                and humidity_pct >= float(self.settings.COMFORT_TRIGGER_HUMIDITY_PCT)
            )
        )

        for key, value in (extra_state or {}).items():
            state.setdefault(key, value)
        return state

    def _on_enter_room(self, meta: dict[str, Any]) -> None:
        entity_id = self.settings.ENTRY_LIGHT_ENTITY_ID
        quiet = bool(meta.get("quiet_hours", False))
        domain = entity_id.split(".", 1)[0]

        # Switch can’t dim: skip during quiet hours to avoid blasting light at night
        if quiet and domain == "switch":
            self.kv.append_event("enter_room_skipped_quiet_hours_switch", {"entity_id": entity_id})
            return

        # Skip if already on
        already_on = False
        if hasattr(self.runner.executor, "read_entity_state"):
            st = self.runner.executor.read_entity_state(entity_id)
            already_on = str(st.get("state", "")).lower() == "on"

        if already_on:
            self.kv.append_event("enter_room_skipped_already_on", {"entity_id": entity_id})
            return

        state = {
            "presence": True,
            "guest_mode": bool(self.kv.get("prefs", "guest_mode", False)),
        }

        plan = self.orchestrator.handle_request(
            intent="enter_room",
            args={"entity_id": entity_id},
            state=state,
        )

        self.runner.execute_actions(
            correlation_id=plan["correlation_id"],
            actions=plan["actions"],
            cooldown_key=plan.get("cooldown_key"),
            cooldown_seconds=int(plan.get("cooldown_seconds", 0)),
        )

    def _on_room_vacant(self, meta: dict[str, Any]) -> None:
        entity_id = self.settings.ENTRY_LIGHT_ENTITY_ID
        domain = entity_id.split(".", 1)[0]

        if domain not in {"switch", "light"}:
            self.kv.append_event("vacancy_off_skipped_unsupported_entity", {"entity_id": entity_id})
            return

        if bool(self.kv.get("belief", "presence", False)):
            self.kv.append_event("vacancy_off_skipped_presence_returned", {"entity_id": entity_id})
            return

        already_on = False
        if hasattr(self.runner.executor, "read_entity_state"):
            st = self.runner.executor.read_entity_state(entity_id)
            already_on = str(st.get("state", "")).lower() == "on"

        if not already_on:
            self.kv.append_event("vacancy_off_skipped_already_off", {"entity_id": entity_id})
            return

        tool_name = "switch.set" if domain == "switch" else "light.set"
        off_args = {"entity_id": entity_id, "state": "off"}
        if tool_name == "light.set":
            off_args["state"] = "off"

        correlation_id = new_correlation_id()
        self.runner.execute_actions(
            correlation_id=correlation_id,
            actions=[
                ToolCall(
                    tool=tool_name,
                    args=off_args,
                    idempotency_key=new_idempotency_key(),
                    correlation_id=correlation_id,
                )
            ],
            cooldown_key=None,
            cooldown_seconds=0,
        )
        self.kv.append_event("vacancy_off_executed", {"entity_id": entity_id, **meta})


def _build_executor(
    settings: Settings, logger: JsonlLogger
) -> ToolExecutor | HAToolClientHTTP | HAToolClientReal:
    backend = settings.TOOL_BACKEND.strip().lower()
    mode = settings.AGENT_MODE.strip().lower()

    if backend == "local":
        return ToolExecutor(mode=mode, logger=logger)
    if backend == "http":
        return HAToolClientHTTP(base_url=settings.HA_BASE_URL, mode=mode)
    if backend == "ha":
        # THIS is the real Home Assistant client
        return HAToolClientReal(
            base_url=settings.HA_BASE_URL,
            token=settings.HA_TOKEN,
            logger=logger,
            mode=mode,
            timeout_s=20,  # real HA calls can be slower, especially with TTS; increase timeout
        )

    raise ValueError(f"Unsupported TOOL_BACKEND '{settings.TOOL_BACKEND}'. Use 'local' or 'http'.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings()
    agent_state = AgentAppState(settings)
    app.state.agent = agent_state

    # Prime TTS cache for common phrases
    if isinstance(agent_state.runner.executor, HAToolClientReal):
        for phrase in ["Fan on.", "Fan off.", "Denied.", "Guest mode."]:
            agent_state.runner.executor.prime_tts(phrase)

    # Start MQTT listener
    agent_state.mqtt.start()

    try:
        yield
    finally:
        agent_state.mqtt.stop()


app = FastAPI(title="Bedroom Agent", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    state = app.state.agent
    return {
        "ok": True,
        "mode": state.settings.AGENT_MODE,
        "backend": state.settings.TOOL_BACKEND,
    }


@app.post("/agent/run")
def run_agent(req: AgentRunRequest) -> dict[str, Any]:
    try:
        state = app.state.agent.build_runtime_state(req.state)
        plan = app.state.agent.orchestrator.handle_request(
            intent=req.intent,
            args=req.args,
            state=state,
        )
        execution = app.state.agent.runner.execute_actions(
            correlation_id=plan["correlation_id"],
            actions=plan["actions"],
            cooldown_key=plan.get("cooldown_key"),
            cooldown_seconds=int(plan.get("cooldown_seconds", 0)),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "correlation_id": plan["correlation_id"],
        "decision": plan["decision"].model_dump(),
        "actions": [a.model_dump() for a in plan["actions"]],
        "execution": execution,
    }


@app.post("/agent/chat")
def chat(req: AgentChatRequest) -> dict[str, Any]:
    """Natural language endpoint.

    For hackathon scope:
    - route text -> intent
    - reuse existing policy+runner pipeline

    Later you'll extend intents like analyze_bedroom/focus_start/etc.
    """
    try:
        state = app.state.agent.build_runtime_state(req.state)
        intent, args = app.state.agent.router.route(text=req.text, state=state)
        if intent == "status":
            result = app.state.agent.status_service.handle_query(args.get("query", req.text))
            return {
                "mode": "info",
                "input": {"text": req.text, "intent": intent, "args": args},
                "result": result,
            }

        if intent == "analyze_bedroom":
            result = app.state.agent.room_analyzer.analyze(req.text)
            return {
                "mode": "info",
                "input": {"text": req.text, "intent": intent, "args": args},
                "result": result,
            }

        if intent == "decision_request":
            agent = app.state.agent
            agent.kv.append_event(
                "llm_decision_requested",
                {
                    "source": "user_chat",
                    "trigger": "chat_request",
                    "user_text": req.text,
                    "temperature_c": state.get("temperature_c"),
                    "humidity_pct": state.get("humidity_pct"),
                },
            )
            choice = agent.decision_engine.choose_intent(
                source="user_chat",
                trigger="chat_request",
                user_text=req.text,
                state=state,
            )
            agent.kv.append_event(
                "llm_decision_returned",
                {
                    "source": choice.source,
                    "trigger": choice.trigger,
                    "user_text": req.text,
                    "chosen_intent": choice.intent,
                    "confidence": choice.confidence,
                    "rationale": choice.rationale,
                    "reasoning_tags": choice.reasoning_tags,
                    "fallback_used": choice.fallback_used,
                    "temperature_c": state.get("temperature_c"),
                    "humidity_pct": state.get("humidity_pct"),
                    "ac_available": state.get("ac_available"),
                },
            )
            if choice.fallback_used:
                agent.kv.append_event(
                    "llm_decision_fallback_used",
                    {"user_text": req.text, "chosen_intent": choice.intent},
                )

            if choice.intent == "no_action":
                agent.kv.append_event(
                    "llm_intent_executed",
                    {"chosen_intent": choice.intent, "executed_tools": [], "success": True},
                )
                return {
                    "mode": "action",
                    "input": {"text": req.text, "intent": intent, "args": args},
                    "decision": {
                        "chosen_intent": choice.intent,
                        "confidence": choice.confidence,
                        "rationale": choice.rationale,
                        "reasoning_tags": choice.reasoning_tags,
                        "fallback_used": choice.fallback_used,
                    },
                    "policy": {"decision": "allow", "reason": "no_action"},
                    "actions": [],
                    "execution": {"success": True, "failures": [], "executed_tools": []},
                }

            plan = agent.orchestrator.handle_request(intent=choice.intent, args=choice.args, state=state)
            policy = plan["decision"].model_dump()
            if plan["decision"].decision == "deny":
                agent.kv.append_event(
                    "llm_intent_rejected_by_policy",
                    {
                        "chosen_intent": choice.intent,
                        "reason": plan["decision"].reason,
                        "temperature_c": state.get("temperature_c"),
                        "humidity_pct": state.get("humidity_pct"),
                    },
                )
            execution = agent.runner.execute_actions(
                correlation_id=plan["correlation_id"],
                actions=plan["actions"],
                cooldown_key=plan.get("cooldown_key"),
                cooldown_seconds=int(plan.get("cooldown_seconds", 0)),
            )
            agent.kv.append_event(
                "llm_intent_executed",
                {
                    "chosen_intent": choice.intent,
                    "policy_decision": policy["decision"],
                    "policy_reason": policy["reason"],
                    "executed_tools": execution.get("executed_tools", []),
                    "success": execution.get("success", False),
                },
            )
            return {
                "mode": "action",
                "input": {"text": req.text, "intent": intent, "args": args},
                "decision": {
                    "chosen_intent": choice.intent,
                    "confidence": choice.confidence,
                    "rationale": choice.rationale,
                    "reasoning_tags": choice.reasoning_tags,
                    "fallback_used": choice.fallback_used,
                },
                "policy": policy,
                "actions": [a.model_dump() for a in plan["actions"]],
                "execution": execution,
            }

        plan = app.state.agent.orchestrator.handle_request(intent=intent, args=args, state=state)
        execution = app.state.agent.runner.execute_actions(
            correlation_id=plan["correlation_id"],
            actions=plan["actions"],
            cooldown_key=plan.get("cooldown_key"),
            cooldown_seconds=int(plan.get("cooldown_seconds", 0)),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "mode": "action",
        "input": {"text": req.text, "intent": intent, "args": args},
        "correlation_id": plan["correlation_id"],
        "decision": plan["decision"].model_dump(),
        "actions": [a.model_dump() for a in plan["actions"]],
        "execution": execution,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=9000, reload=False)
