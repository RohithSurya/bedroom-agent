# Contracts

This document describes the stable interfaces between Home Assistant, the bedroom-agent service, internal tool execution, and local persistence.

## HTTP API

Base service: `apps/bedroom-agent/src/app.py`

### `GET /health`

Response:

```json
{
  "ok": true,
  "mode": "shadow",
  "backend": "ha"
}
```

- `mode` is `shadow` or `active`
- `backend` is the configured tool backend, typically `ha`

### `POST /agent/run`

Purpose: execute a known intent through the deterministic orchestrator.

Request body:

```json
{
  "intent": "fan_on",
  "args": {},
  "state": {
    "guest_mode": false
  }
}
```

Allowed `intent` values:

- `night_mode`
- `fan_on`
- `fan_off`
- `enter_room`
- `sleep_mode`
- `focus_start`
- `focus_end`
- `comfort_adjust`
- `no_action`

Successful response shape:

```json
{
  "correlation_id": "cid_...",
  "decision": {
    "decision": "allow",
    "reason": "ok",
    "cooldown_seconds": 90,
    "safety_checks": []
  },
  "actions": [
    {
      "tool": "fan.set",
      "args": {
        "entity_id": "fan.bedroom_fan",
        "state": "on"
      },
      "idempotency_key": "idem_...",
      "correlation_id": "cid_..."
    }
  ],
  "execution": {
    "success": true,
    "failures": [],
    "executed_tools": ["fan.set", "tts.say"]
  }
}
```

Failure mode:

- HTTP `500` when the application raises unexpectedly

### `POST /agent/chat`

Purpose: accept natural language, route it to a high-level intent, and either answer directly or execute an action flow.

Request body:

```json
{
  "text": "analyze bedroom",
  "state": {
    "guest_mode": false
  }
}
```

Possible routed intents:

- `night_mode`
- `fan_on`
- `fan_off`
- `sleep_mode`
- `focus_start`
- `focus_end`
- `comfort_adjust`
- `analyze_bedroom`
- `status`
- `decision_request`

Info response shape:

```json
{
  "mode": "info",
  "input": {
    "text": "Analyze bedroom",
    "intent": "analyze_bedroom",
    "args": {}
  },
  "result": {
    "summary": "The desk is active and the bed is mostly made.",
    "structured": {}
  }
}
```

Standard action response shape:

```json
{
  "mode": "action",
  "input": {
    "text": "start focus mode",
    "intent": "focus_start",
    "args": {}
  },
  "correlation_id": "cid_...",
  "decision": {
    "decision": "allow",
    "reason": "ok",
    "cooldown_seconds": 90,
    "safety_checks": []
  },
  "actions": [],
  "execution": {
    "success": true,
    "failures": [],
    "executed_tools": []
  }
}
```

For `decision_request`, the action response shape is slightly different:

```json
{
  "mode": "action",
  "input": {
    "text": "what should happen now",
    "intent": "decision_request",
    "args": {}
  },
  "decision": {
    "chosen_intent": "comfort_adjust",
    "confidence": 0.81,
    "rationale": "The room is above the comfort threshold.",
    "reasoning_tags": ["temperature", "comfort"],
    "fallback_used": false
  },
  "policy": {
    "decision": "allow",
    "reason": "ok",
    "cooldown_seconds": 90,
    "safety_checks": []
  },
  "actions": [],
  "execution": {
    "success": true,
    "failures": [],
    "executed_tools": []
  }
}
```

The `decision` object in that path contains:

- `chosen_intent`
- `confidence`
- `rationale`
- `reasoning_tags`
- `fallback_used`

## Internal Tool Contract

Defined in `src/contracts/ha.py`.

### `ToolCall`

```json
{
  "tool": "fan.set",
  "args": {
    "entity_id": "fan.bedroom_fan",
    "state": "on"
  },
  "idempotency_key": "idem_...",
  "correlation_id": "cid_..."
}
```

### `ToolResult`

```json
{
  "ok": true,
  "tool": "fan.turn_on",
  "details": {
    "status": 200
  }
}
```

Supported tool names in the current real HA client:

- `fan.set`
- `light.set`
- `switch.set`
- `climate.set_mode`
- `climate.set_temperature`
- `climate.set_fan_mode`
- `tts.say`

## Policy Contract

Defined in `src/contracts/policy.py`.

```json
{
  "decision": "allow",
  "reason": "ok",
  "cooldown_seconds": 90,
  "safety_checks": []
}
```

- `decision` is `allow` or `deny`
- `reason` is machine-readable and used by Home Assistant to build spoken responses
- `cooldown_seconds` is advisory plus enforcement input
- `safety_checks` is a list of applied checks

## Runtime State Contract

The app derives runtime state in `AgentAppState.build_runtime_state()`.

Current derived keys include:

- `presence`
- `door_open`
- `guest_mode`
- `temperature_entity_id`
- `humidity_entity_id`
- `temperature_c`
- `humidity_pct`
- `light_entity_id`
- `light_state`
- `bedroom_lamp_entity_id`
- `bedroom_lamp_state`
- `fan_entity_id`
- `fan_state`
- `ac_entity_id`
- `ac_available`
- `ac_state`
- `ac_hvac_mode`
- `ac_target_temp_c`
- `ac_fan_mode`
- `comfort_trigger_temp_c`
- `comfort_trigger_humidity_pct`
- `comfort_target_temp_c`
- `sleep_target_temp_c`
- `focus_mode_enable_fan`
- `focus_mode_enable_climate`
- `sleep_mode_enable_climate`
- `comfort_use_fan_fallback`
- `room_uncomfortable`
- `vision`

`extra_state` provided by callers is merged with `setdefault()`. That means caller-provided keys only fill missing values and do not override values already derived from Home Assistant or local memory.

## Persistence Contract

Storage backend: `src/memory/sqlite_kv.py`

Tables:

- `kv(namespace, key, value_json, updated_at)`
- `events(id, ts, type, payload_json)`

Namespaces used by the app today:

- `belief`
  - examples: `presence`, `door_open`
- `prefs`
  - examples: `guest_mode`
- `vision`
  - `latest_bedroom_analysis`
- `status`
  - `last_summary`

JSONL log:

- file: `logs/events.jsonl`
- row shape:

```json
{
  "ts": "2026-03-01T10:20:30-05:00",
  "cid": "cid_...",
  "type": "tool_result",
  "payload": {}
}
```

## Important Event Types

Stored in SQLite `events` and used by status/decision logic:

- `door_update`
- `presence_update`
- `enter_detected`
- `enter_room_skipped_already_on`
- `vacancy_detected`
- `vacancy_off_executed`
- `bedroom_analysis_completed`
- `bedroom_analysis_failed`
- `llm_decision_requested`
- `llm_decision_returned`
- `llm_decision_fallback_used`
- `llm_intent_rejected_by_policy`
- `llm_intent_executed`
- `status_query_answered`

## Home Assistant Contract

Current HA integration lives in `infra/home-automation/ha_config`.

### `rest_command`

`configuration.yaml` defines:

- `rest_command.bedroom_agent_run` -> `POST /agent/run`
- `rest_command.bedroom_agent_chat` -> `POST /agent/chat`

### Voice conversation automation

`automations.yaml` now uses a catch-all Assist sentence trigger:

- `{command}`

This means every Assist utterance is forwarded to `POST /agent/chat`, where the
bedroom-agent LLM chooses the high-level route.

The automation expects `/agent/chat` to return either:

- `mode=info` with `result.summary`, or
- `mode=action` with `policy` and `decision` details used to build a spoken reply
