from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent.orchestrator import Orchestrator
from agent.runner import Runner
from core.config import Settings
from core.cooldowns import CooldownStore
from core.logging_jsonl import JsonlLogger
from tools.ha_http_client import HAToolClientHTTP
from tools.tool_executor import ToolExecutor
from tools.ha_real_client import HAToolClientReal


class AgentRunRequest(BaseModel):
    intent: Literal["night_mode", "fan_on", "fan_off"]
    args: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)


class AgentAppState:
    def __init__(self, settings: Settings) -> None:
        cooldowns = CooldownStore()
        self.settings = settings
        self.cooldowns = cooldowns
        self.logger = JsonlLogger(log_dir=settings.LOG_DIR, tz_name=settings.TIMEZONE)
        self.orchestrator = Orchestrator(cooldowns=cooldowns)
        self.runner = Runner(
            executor=_build_executor(settings, logger=self.logger),
            cooldowns=cooldowns,
            logger=self.logger,
        )


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

    # Prime TTS cache for common phrases to avoid startup delay
    if isinstance(agent_state.runner.executor, HAToolClientReal):
        for phrase in ["Fan on.", "Fan off.", "Denied.", "Guest mode."]:
            agent_state.runner.executor.prime_tts(phrase)

    yield


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=9000, reload=False)
