from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

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
    intent: Literal["night_mode", "fan_on", "fan_off", "enter_room"]
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
        plan = app.state.agent.orchestrator.handle_request(
            intent=req.intent,
            args=req.args,
            state=req.state,
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
        intent, args = app.state.agent.router.route(text=req.text, state=req.state)
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

        if intent in {"focus_start", "focus_end"}:
            return {
                "mode": "info",
                "input": {"text": req.text, "intent": intent, "args": args},
                "result": {
                    "summary": "Focus mode is not implemented in this demo build yet.",
                    "structured": {"supported": False, "intent": intent},
                },
            }

        plan = app.state.agent.orchestrator.handle_request(intent=intent, args=args, state=req.state)
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
