from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any, Dict

app = FastAPI(title="HA Mock")

# In-memory "HA state"
STATE: Dict[str, Any] = {
    "lights": {"light.bedroom_light": {"state": "off"}},
    "fans": {"fan.bedroom_fan": {"state": "off"}},
    "switches": {},
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

    entity_id = str(req.args.get("entity_id", "light.bedroom_light"))
    state = str(req.args.get("state", "on")).lower()
    if state not in ("on", "off"):
        return {
            "ok": False,
            "tool": "light.set",
            "details": {"error": "invalid_state", "state": state},
        }

    brightness_pct = None
    if "brightness_pct" in req.args:
        try:
            brightness_pct = int(req.args.get("brightness_pct"))
        except Exception:
            return {
                "ok": False,
                "tool": "light.set",
                "details": {
                    "error": "invalid_brightness_pct",
                    "brightness_pct": req.args.get("brightness_pct"),
                },
            }

    transition_s = None
    if "transition_s" in req.args:
        try:
            transition_s = float(req.args.get("transition_s"))
        except Exception:
            return {
                "ok": False,
                "tool": "light.set",
                "details": {
                    "error": "invalid_transition_s",
                    "transition_s": req.args.get("transition_s"),
                },
            }

    STATE["lights"].setdefault(entity_id, {})
    STATE["lights"][entity_id]["state"] = state
    if brightness_pct is not None and state == "on":
        STATE["lights"][entity_id]["brightness_pct"] = brightness_pct
    if transition_s is not None:
        STATE["lights"][entity_id]["transition_s"] = transition_s

    return {
        "ok": True,
        "tool": "light.set",
        "details": {
            "entity_id": entity_id,
            "state": state,
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


@app.post("/tool/fan.set")
def tool_fan_set(req: ToolRequest):
    injected = maybe_fail("fan.set")
    if injected:
        return injected

    entity_id = str(req.args.get("entity_id", "fan.bedroom_fan"))
    state = str(req.args.get("state", "off")).lower()
    if state not in ("on", "off"):
        return {
            "ok": False,
            "tool": "fan.set",
            "details": {"error": "invalid_state", "state": state},
        }

    STATE["fans"].setdefault(entity_id, {})
    STATE["fans"][entity_id]["state"] = state
    return {
        "ok": True,
        "tool": "fan.set",
        "details": {"entity_id": entity_id, "state": state},
    }
