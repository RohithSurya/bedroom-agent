from __future__ import annotations

from contextlib import asynccontextmanager
import time
import requests
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from pathlib import Path

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
from llm.factory import build_llm_client
from tools.ha_http_client import HAToolClientHTTP
from tools.tool_executor import ToolExecutor
from tools.ha_real_client import HAToolClientReal
from vision.image_source import BedroomImageSource
from vision.room_analyzer import BedroomRoomAnalyzer
from reliability.deadline import Deadline
from memory.tiered_memory import TieredMemory
from memory.preference_feedback import PreferenceFeedback


class AgentRunRequest(BaseModel):
    intent: Literal[
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

        def _make_llm_client(timeout_s: float):
            return build_llm_client(
                model=settings.LLM_MODEL,
                timeout_s=timeout_s,
                base_url=settings.LLM_BASE_URL,
                openai_api_key=settings.OPENAI_API_KEY,
            )

        # Optional LLM backend (local on-device). If it isn't running, routing falls back safely.
        self.llm = _make_llm_client(timeout_s=float(settings.LLM_TIMEOUT_S))
        self.decision_llm = (
            _make_llm_client(timeout_s=float(settings.LLM_DECISION_TIMEOUT_S))
            if bool(settings.LLM_DECISION_ENABLED)
            else None
        )
        self.router = NLRouter(llm=self.llm)
        self.kv = SqliteKV(settings.SQLITE_PATH)
        self.memory = TieredMemory(kv=self.kv)
        self.preference_feedback = PreferenceFeedback(kv=self.kv)
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

    def _required_entity_ids_for_intent(self, intent: str | None) -> set[str]:
        # If intent is None, default to “full” state (keeps old behavior for any callers)
        if not intent or intent.strip().lower() == "status":
            return {
                self.settings.ENTRY_LIGHT_ENTITY_ID,
                self.settings.BEDROOM_LAMP_ENTITY_ID,
                self.settings.BEDROOM_FAN_ENTITY_ID,
                self.settings.BEDROOM_AC_ENTITY_ID,
                self.settings.TEMP_SENSOR_ENTITY_ID,
                self.settings.HUMIDITY_SENSOR_ENTITY_ID,
            } - {None, ""}

        intent = intent.strip().lower()

        # Minimal HA reads by intent
        env_intents = {"comfort_adjust", "focus_start", "sleep_mode", "decision_request"}
        light_check_intents = {"focus_start", "sleep_mode"}

        ids: set[str] = set()

        if intent in light_check_intents:
            if self.settings.ENTRY_LIGHT_ENTITY_ID:
                ids.add(self.settings.ENTRY_LIGHT_ENTITY_ID)
            if self.settings.BEDROOM_LAMP_ENTITY_ID:
                ids.add(self.settings.BEDROOM_LAMP_ENTITY_ID)

        if intent in env_intents:
            if self.settings.BEDROOM_AC_ENTITY_ID:
                ids.add(self.settings.BEDROOM_AC_ENTITY_ID)
            if self.settings.TEMP_SENSOR_ENTITY_ID:
                ids.add(self.settings.TEMP_SENSOR_ENTITY_ID)
            if self.settings.HUMIDITY_SENSOR_ENTITY_ID:
                ids.add(self.settings.HUMIDITY_SENSOR_ENTITY_ID)

        # Fan/light generally don’t need pre-reads for fan_on/off/enter_room
        return ids

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

    def build_runtime_state(
        self,
        extra_state: dict[str, Any] | None = None,
        *,
        intent: str | None = None,
        user_text: str | None = None,
    ) -> dict[str, Any]:
        beliefs = self.kv.get_namespace("belief")
        prefs = self.kv.get_namespace("prefs")

        relevant_prefs = self.memory.get_relevant_preferences(
            intent=intent,
            user_text=user_text,
            defaults={
                "sleep.preferred_temp_c": int(self.settings.SLEEP_TARGET_TEMP_C),
                "focus.prefer_fan": bool(self.settings.FOCUS_MODE_ENABLE_FAN),
                "focus.prefer_climate": bool(self.settings.FOCUS_MODE_ENABLE_CLIMATE),
                "comfort.preferred_temp_c": int(self.settings.COMFORT_TARGET_TEMP_C),
                "comfort.prefer_fan": bool(self.settings.COMFORT_USE_FAN_FALLBACK),
                "comfort.prefer_climate": True,
            },
        )
        recent_episodes = self.memory.get_recent_episodes()
        episode_summary = self.memory.get_rolling_summary()

        light_entity_id = self.settings.ENTRY_LIGHT_ENTITY_ID
        bedroom_lamp_entity_id = self.settings.BEDROOM_LAMP_ENTITY_ID
        fan_entity_id = self.settings.BEDROOM_FAN_ENTITY_ID
        ac_entity_id = self.settings.BEDROOM_AC_ENTITY_ID
        temp_entity_id = self.settings.TEMP_SENSOR_ENTITY_ID
        humidity_entity_id = self.settings.HUMIDITY_SENSOR_ENTITY_ID

        required_ids = self._required_entity_ids_for_intent(intent)
        ha_reads = 0

        def read_if_needed(eid: str | None) -> dict[str, Any]:
            nonlocal ha_reads
            if not eid or eid not in required_ids:
                return {}
            ha_reads += 1
            return self.runner.read_entity_state(eid)

        light_raw = read_if_needed(light_entity_id)
        bedroom_lamp_raw = read_if_needed(bedroom_lamp_entity_id)
        fan_raw = read_if_needed(fan_entity_id)
        ac_raw = read_if_needed(ac_entity_id)
        temp_raw = read_if_needed(temp_entity_id)
        humidity_raw = read_if_needed(humidity_entity_id)

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
            "bedroom_lamp_entity_id": bedroom_lamp_entity_id,
            "bedroom_lamp_state": str(bedroom_lamp_raw.get("state", "unknown")).lower(),
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
            # Memory-backed preference context
            "relevant_prefs": relevant_prefs,
            "recent_episodes": recent_episodes,
            "episode_summary": episode_summary,
            "sleep_preferred_temp_c": relevant_prefs.get(
                "sleep.preferred_temp_c",
                int(self.settings.SLEEP_TARGET_TEMP_C),
            ),
            "sleep_prefer_lights_off": bool(relevant_prefs.get("sleep.prefer_lights_off", True)),
            "focus_prefer_fan": bool(
                relevant_prefs.get(
                    "focus.prefer_fan",
                    bool(self.settings.FOCUS_MODE_ENABLE_FAN),
                )
            ),
            "focus_prefer_climate": bool(
                relevant_prefs.get(
                    "focus.prefer_climate",
                    bool(self.settings.FOCUS_MODE_ENABLE_CLIMATE),
                )
            ),
            "focus_preferred_temp_c": relevant_prefs.get(
                "focus.preferred_temp_c",
                int(self.settings.COMFORT_TARGET_TEMP_C),
            ),
            "comfort_preferred_temp_c": relevant_prefs.get(
                "comfort.preferred_temp_c",
                int(self.settings.COMFORT_TARGET_TEMP_C),
            ),
            "comfort_prefer_fan": bool(
                relevant_prefs.get(
                    "comfort.prefer_fan",
                    bool(self.settings.COMFORT_USE_FAN_FALLBACK),
                )
            ),
            "comfort_prefer_climate": bool(relevant_prefs.get("comfort.prefer_climate", True)),
            "vision": self._build_vision_state(),
        }
        state["room_uncomfortable"] = bool(
            (
                temperature_c is not None
                and temperature_c >= float(self.settings.COMFORT_TRIGGER_TEMP_C)
            )
            or (
                humidity_pct is not None
                and humidity_pct >= float(self.settings.COMFORT_TRIGGER_HUMIDITY_PCT)
            )
        )

        for key, value in (extra_state or {}).items():
            state.setdefault(key, value)

        state["_metrics"] = {"ha_reads": ha_reads, "required_ids": sorted(required_ids)}
        return state

    def record_episode(
        self,
        *,
        user_text: str,
        intent: str,
        state: dict[str, Any],
        decision: dict[str, Any],
        actions: list[dict[str, Any]],
        execution: dict[str, Any],
        memory_hits: list[str] | None = None,
    ) -> dict[str, Any]:
        episode = {
            "ts": time.time(),
            "user_text": user_text,
            "intent": intent,
            "memory_hits": memory_hits or [],
            "state_snapshot": {
                "presence": state.get("presence"),
                "temperature_c": state.get("temperature_c"),
                "humidity_pct": state.get("humidity_pct"),
                "light_state": state.get("light_state"),
                "fan_state": state.get("fan_state"),
                "ac_state": state.get("ac_state"),
                "ac_hvac_mode": state.get("ac_hvac_mode"),
                "vision": state.get("vision"),
            },
            "plan_summary": [str(a.get("tool", "")) for a in actions],
            "policy_decision": str(decision.get("decision", "") or ""),
            "policy_reason": str(decision.get("reason", "") or ""),
            "execution_success": bool(execution.get("success", False)),
        }
        return self.memory.record_episode(episode)

    def _on_enter_room(self, meta: dict[str, Any]) -> None:
        entity_id = self.settings.ENTRY_LIGHT_ENTITY_ID

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
            args={"light_entity_id": entity_id},
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

        correlation_id = new_correlation_id()
        self.runner.execute_actions(
            correlation_id=correlation_id,
            actions=[
                ToolCall(
                    tool="light.set",
                    args={"entity_id": entity_id, "state": "off"},
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

    raise ValueError(
        f"Unsupported TOOL_BACKEND '{settings.TOOL_BACKEND}'. Use 'local', 'http', or 'ha'."
    )


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


def _resolve_path(raw_path: str) -> Path:
    p = Path(raw_path)
    return p if p.is_absolute() else (Path.cwd() / p).resolve()


def _check_vision(agent: AgentAppState) -> dict[str, Any]:
    if not bool(agent.settings.VISION_ANALYSIS_ENABLED):
        return {
            "ok": True,
            "skipped": True,
            "reason": "vision disabled",
        }

    camera_mode = str(agent.settings.CAMERA_MODE).strip().lower()

    if camera_mode != "file":
        return {
            "ok": True,
            "skipped": True,
            "reason": f"camera_mode={camera_mode}",
        }

    raw_path = str(agent.settings.VISION_FALLBACK_IMAGE_PATH)
    path = _resolve_path(raw_path)
    exists = path.exists() and path.is_file()

    return {
        "ok": exists,
        "camera_mode": camera_mode,
        "path": str(path),
        "exists": exists,
    }


def _check_tool_backend(agent: AgentAppState) -> dict[str, Any]:
    backend = str(agent.settings.TOOL_BACKEND).strip().lower()
    executor = agent.runner.executor

    if backend == "local":
        return {
            "ok": isinstance(executor, ToolExecutor),
            "backend": backend,
            "executor_type": type(executor).__name__,
        }

    if backend == "http":
        if not isinstance(executor, HAToolClientHTTP):
            return {
                "ok": False,
                "backend": backend,
                "error": f"unexpected executor type: {type(executor).__name__}",
            }

        base_url = str(agent.settings.HA_BASE_URL).rstrip("/")
        candidates = [
            f"{base_url}/health",
            f"{base_url}/healthz",
            f"{base_url}/",
        ]

        last_error = None
        for url in candidates:
            try:
                resp = requests.get(url, timeout=2)
                if resp.ok:
                    return {
                        "ok": True,
                        "backend": backend,
                        "url": url,
                        "status_code": resp.status_code,
                    }
                last_error = f"{url} -> {resp.status_code}"
            except Exception as e:
                last_error = f"{url} -> {e}"

        return {
            "ok": False,
            "backend": backend,
            "error": last_error or "http backend probe failed",
        }

    if backend == "ha":
        if not isinstance(executor, HAToolClientReal):
            return {
                "ok": False,
                "backend": backend,
                "error": f"unexpected executor type: {type(executor).__name__}",
            }

        base_url = str(agent.settings.HA_BASE_URL).rstrip("/")
        token = str(agent.settings.HA_TOKEN)

        if not base_url or not token:
            return {
                "ok": False,
                "backend": backend,
                "error": "missing HA_BASE_URL or HA_TOKEN",
            }

        try:
            resp = requests.get(
                f"{base_url}/api/",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=2,
            )
            return {
                "ok": resp.status_code == 200,
                "backend": backend,
                "url": f"{base_url}/api/",
                "status_code": resp.status_code,
            }
        except Exception as e:
            return {
                "ok": False,
                "backend": backend,
                "error": f"ha backend probe failed: {e}",
            }

    return {
        "ok": False,
        "backend": backend,
        "error": f"unsupported backend '{backend}'",
    }


def _check_mqtt(agent: AgentAppState) -> dict[str, Any]:
    mqtt_listener = agent.mqtt
    return {
        "ok": bool(mqtt_listener.connected),
        "connected": bool(mqtt_listener.connected),
        "host": mqtt_listener.mqtt_host,
        "port": mqtt_listener.mqtt_port,
        "door_topic": mqtt_listener.door_topic,
        "presence_topic": mqtt_listener.presence_topic,
    }


def _check_llm(agent: AgentAppState) -> dict[str, Any]:
    base_url = str(agent.settings.LLM_BASE_URL or "").rstrip("/")

    if not base_url:
        return {
            "ok": True,
            "skipped": True,
            "reason": "LLM_BASE_URL not configured",
        }

    headers = {}
    api_key = str(agent.settings.OPENAI_API_KEY or "").strip()
    if api_key and api_key not in {"token", "YOUR_API_KEY_HERE"}:
        headers["Authorization"] = f"Bearer {api_key}"

    candidates = [
        f"{base_url}/models",
        f"{base_url}/health",
    ]

    last_error = None
    for url in candidates:
        try:
            resp = requests.get(url, headers=headers, timeout=2)
            if resp.ok:
                return {
                    "ok": True,
                    "url": url,
                    "status_code": resp.status_code,
                }
            last_error = f"{url} -> {resp.status_code}"
        except Exception as e:
            last_error = f"{url} -> {e}"

    return {
        "ok": False,
        "error": last_error or "llm probe failed",
    }


@app.get("/health")
def health() -> dict[str, Any]:
    state = app.state.agent
    return {
        "ok": True,
        "mode": state.settings.AGENT_MODE,
        "backend": state.settings.TOOL_BACKEND,
    }


@app.get("/readyz")
def readyz(response: Response) -> dict[str, Any]:
    agent = app.state.agent

    checks = {
        "tool_backend": _check_tool_backend(agent),
        "mqtt": _check_mqtt(agent),
        "llm": _check_llm(agent),
        "vision": _check_vision(agent),
    }

    ok = all(check["ok"] for check in checks.values())
    response.status_code = 200 if ok else 503

    return {
        "ok": ok,
        "mode": agent.settings.AGENT_MODE,
        "backend": agent.settings.TOOL_BACKEND,
        "checks": checks,
    }


@app.post("/agent/run")
def run_agent(req: AgentRunRequest) -> dict[str, Any]:
    try:
        deadline = Deadline.from_now(app.state.agent.settings.REQUEST_BUDGET_S)
        t0 = time.perf_counter()
        state = app.state.agent.build_runtime_state(req.state, intent=req.intent)
        t_state = time.perf_counter()
        plan = app.state.agent.orchestrator.handle_request(
            intent=req.intent,
            args=req.args,
            state=state,
        )
        t_plan = time.perf_counter()
        execution = app.state.agent.runner.execute_actions(
            correlation_id=plan["correlation_id"],
            actions=plan["actions"],
            cooldown_key=plan.get("cooldown_key"),
            cooldown_seconds=int(plan.get("cooldown_seconds", 0)),
            deadline=deadline,
        )
        t_exec = time.perf_counter()
        app.state.agent.record_episode(
            user_text=f"/agent/run:{req.intent}",
            intent=req.intent,
            state=state,
            decision=plan["decision"].model_dump(),
            actions=[a.model_dump() for a in plan["actions"]],
            execution=execution,
            memory_hits=list(state.get("relevant_prefs", {}).keys()),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "correlation_id": plan["correlation_id"],
        "decision": plan["decision"].model_dump(),
        "actions": [a.model_dump() for a in plan["actions"]],
        "execution": execution,
        "timings_ms": {
            "state": round((t_state - t0) * 1000, 1),
            "plan": round((t_plan - t_state) * 1000, 1),
            "exec": round((t_exec - t_plan) * 1000, 1),
        },
        "state_metrics": state.get("_metrics", {}),
    }


@app.post("/agent/chat")
def chat(req: AgentChatRequest) -> dict[str, Any]:
    """Natural language endpoint.

    For hackathon scope:
    - route text -> intent
    - reuse existing policy+runner pipeline
    - return info about routing + execution for better observability"""
    try:
        deadline = Deadline.from_now(app.state.agent.settings.REQUEST_BUDGET_S)
        t0 = time.perf_counter()
        intent, args = app.state.agent.router.route(text=req.text, state={})
        last_episode = app.state.agent.memory.get_last_episode()
        feedback_result = app.state.agent.preference_feedback.apply(
            user_text=req.text,
            last_episode=last_episode,
        )
        if feedback_result is not None:
            return {
                "mode": "memory_update",
                "message": feedback_result["message"],
                "updates": feedback_result["updates"],
                "last_intent": feedback_result["intent_scope"],
            }
        t_nl_router = time.perf_counter()

        t_before_state = time.perf_counter()
        state = app.state.agent.build_runtime_state(req.state, intent=intent, user_text=req.text)
        t_state = time.perf_counter()

        if intent == "status":
            result = app.state.agent.status_service.handle_query(
                args.get("query", req.text),
                runtime_state=state,
            )
            t_status = time.perf_counter()
            return {
                "mode": "info",
                "input": {"text": req.text, "intent": intent, "args": args},
                "result": result,
                "timings_ms": {
                    "nl_router": round((t_nl_router - t0) * 1000, 1),
                    "state": round((t_state - t_before_state) * 1000, 1),
                    "status_service": round((t_status - t_state) * 1000, 1),
                },
            }

        if intent == "analyze_bedroom":
            result = app.state.agent.room_analyzer.analyze(req.text)
            t_analyze_bedroom = time.perf_counter()
            return {
                "mode": "info",
                "input": {"text": req.text, "intent": intent, "args": args},
                "result": result,
                "timings_ms": {
                    "nl_router": round((t_nl_router - t0) * 1000, 1),
                    "analyze_bedroom": round((t_analyze_bedroom - t_nl_router) * 1000, 1),
                },
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
            t_decision = time.perf_counter()
            decision_record = {
                "intent": choice.intent,
                "confidence": choice.confidence,
                "rationale": choice.rationale,
                "reasoning_tags": choice.reasoning_tags,
                "fallback_used": choice.fallback_used,
                "trace": choice.trace,
            }
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
                    "trace": choice.trace,
                },
            )
            agent.kv.set(
                "decision",
                "last_trace",
                choice.trace,
            )
            agent.kv.set(
                "decision",
                "last_choice",
                decision_record,
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
                agent.record_episode(
                    user_text=req.text,
                    intent=choice.intent,
                    state=state,
                    decision={"decision": "allow", "reason": "no_action"},
                    actions=[],
                    execution={"success": True, "failures": [], "executed_tools": []},
                    memory_hits=list(state.get("relevant_prefs", {}).keys()),
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
                        "trace": choice.trace,
                    },
                    "policy": {"decision": "allow", "reason": "no_action"},
                    "actions": [],
                    "execution": {"success": True, "failures": [], "executed_tools": []},
                    "timings_ms": {
                        "nl_router": round((t_nl_router - t0) * 1000, 1),
                        "state": round((t_state - t_before_state) * 1000, 1),
                        "decision": round((t_decision - t_state) * 1000, 1),
                    },
                }

            plan = agent.orchestrator.handle_request(
                intent=choice.intent, args=choice.args, state=state
            )
            t_plan = time.perf_counter()
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
                deadline=deadline,
            )
            agent.record_episode(
                user_text=req.text,
                intent=choice.intent,
                state=state,
                decision=policy,
                actions=[a.model_dump() for a in plan["actions"]],
                execution=execution,
                memory_hits=list(state.get("relevant_prefs", {}).keys()),
            )
            t_exec = time.perf_counter()
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
                "timings_ms": {
                    "nl_router": round((t_nl_router - t0) * 1000, 1),
                    "state": round((t_state - t_before_state) * 1000, 1),
                    "decision": round((t_decision - t_state) * 1000, 1),
                    "plan": round((t_plan - t_decision) * 1000, 1),
                    "exec": round((t_exec - t_plan) * 1000, 1),
                },
            }

        plan = app.state.agent.orchestrator.handle_request(intent=intent, args=args, state=state)
        app.state.agent.kv.set(
            "decision",
            "last_choice",
            {
                "intent": intent,
                "confidence": 1.0,
                "rationale": f"Direct router matched the request to {intent}.",
                "reasoning_tags": ["router_direct", intent],
                "fallback_used": False,
            },
        )
        app.state.agent.kv.set(
            "decision",
            "last_trace",
            {
                "goal": req.text,
                "selected_intent": intent,
                "selected_because": f"The request was directly matched to {intent}.",
                "reasoning_tags": ["router_direct", intent],
                "memory_hits": list(state.get("relevant_prefs", {}).keys())[:5],
                "episode_summary": str(state.get("episode_summary", "") or ""),
                "signals": [
                    f"presence={bool(state.get('presence', False))}",
                    f"temperature_c={state.get('temperature_c')}",
                    f"humidity_pct={state.get('humidity_pct')}",
                    f"ac_available={bool(state.get('ac_available', False))}",
                ],
                "guardrails": [
                    "guest_mode_on" if bool(state.get("guest_mode", False)) else "guest_mode_off",
                    "sleep_mode_enable_climate"
                    if bool(state.get("sleep_mode_enable_climate", False))
                    else "sleep_mode_climate_disabled",
                ],
                "fallback_used": False,
            },
        )
        t_plan = time.perf_counter()
        execution = app.state.agent.runner.execute_actions(
            correlation_id=plan["correlation_id"],
            actions=plan["actions"],
            cooldown_key=plan.get("cooldown_key"),
            cooldown_seconds=int(plan.get("cooldown_seconds", 0)),
            deadline=deadline,
        )
        app.state.agent.record_episode(
            user_text=req.text,
            intent=intent,
            state=state,
            decision=plan["decision"].model_dump(),
            actions=[a.model_dump() for a in plan["actions"]],
            execution=execution,
            memory_hits=list(state.get("relevant_prefs", {}).keys()),
        )
        t_exec = time.perf_counter()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "mode": "action",
        "input": {"text": req.text, "intent": intent, "args": args},
        "correlation_id": plan["correlation_id"],
        "decision": plan["decision"].model_dump(),
        "actions": [a.model_dump() for a in plan["actions"]],
        "execution": execution,
        "timings_ms": {
            "nl_router": round((t_nl_router - t0) * 1000, 1),
            "state": round((t_state - t_before_state) * 1000, 1),
            "plan": round((t_plan - t_state) * 1000, 1),
            "exec": round((t_exec - t_plan) * 1000, 1),
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=9000, reload=False)
