from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any, Dict

app = FastAPI(title="HA Mock")

# In-memory "HA state"
STATE: Dict[str, Any] = {
    "lights": {"light.bedroom_lamp": {"brightness_pct": 100, "transition_s": 0}},
    "tts": [],
}

# tool -> {"remaining": int, "error": str}
FAILURES: Dict[str, Dict[str, Any]] = {}


class ToolRequest(BaseModel):
    correlation_id: str
    idempotency_key: str
    args: Dict[str, Any] = {}


class InjectFailure(BaseModel):
    tool: str
    times: int = 1
    error: str = "simulated_error"


@app.get("/state")
def get_state():
    return {"ok": True, "state": STATE}


@app.post("/failures/inject")
def inject_failure(req: InjectFailure):
    FAILURES[req.tool] = {"remaining": int(req.times), "error": req.error}
    return {"ok": True, "failure_plan": FAILURES[req.tool]}


def maybe_fail(tool: str):
    plan = FAILURES.get(tool)
    if not plan:
        return None
    if plan["remaining"] <= 0:
        return None
    plan["remaining"] -= 1
    return {
        "ok": False,
        "tool": tool,
        "details": {
            "injected": True,
            "error": plan["error"],
            "remaining": plan["remaining"],
        },
    }


@app.post("/tool/light.set")
def tool_light_set(req: ToolRequest):
    injected = maybe_fail("light.set")
    if injected:
        return injected

    entity_id = str(req.args.get("entity_id", "light.bedroom_lamp"))
    brightness_pct = int(req.args.get("brightness_pct", 15))
    transition_s = float(req.args.get("transition_s", 0))

    STATE["lights"].setdefault(entity_id, {})
    STATE["lights"][entity_id]["brightness_pct"] = brightness_pct
    STATE["lights"][entity_id]["transition_s"] = transition_s

    return {
        "ok": True,
        "tool": "light.set",
        "details": {
            "entity_id": entity_id,
            "brightness_pct": brightness_pct,
            "transition_s": transition_s,
        },
    }


@app.post("/tool/tts.say")
def tool_tts(req: ToolRequest):
    injected = maybe_fail("tts.say")
    if injected:
        return injected

    msg = str(req.args.get("message", ""))
    STATE["tts"].append(msg)
    return {"ok": True, "tool": "tts.say", "details": {"message": msg}}


@app.post("/tool/switch.set")
def tool_switch_set(req: ToolRequest):
    injected = maybe_fail("switch.set")
    if injected:
        return injected

    entity_id = str(req.args.get("entity_id"))
    state = str(req.args.get("state", "off")).lower()
    if state not in ("on", "off"):
        return {
            "ok": False,
            "tool": "switch.set",
            "details": {"error": "invalid_state", "state": state},
        }

    STATE["switches"].setdefault(entity_id, {})
    STATE["switches"][entity_id]["state"] = state
    return {
        "ok": True,
        "tool": "switch.set",
        "details": {"entity_id": entity_id, "state": state},
    }
