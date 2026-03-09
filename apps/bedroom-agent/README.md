# bedroom-agent service

FastAPI runtime for the bedroom automation agent. This service owns request routing, runtime-state assembly, deterministic orchestration, tool execution, background MQTT listeners, and local memory.

## Responsibilities

- Serve `GET /health` and `GET /readyz`
- Accept `POST /agent/run` for direct intents
- Accept `POST /agent/chat` for natural-language requests
- Build runtime context from Home Assistant state, beliefs, preferences, recent episodes, and cached vision output
- Enforce policy gates and cooldowns before tool execution
- Persist beliefs, preferences, decision traces, recent episodes, status summaries, and vision analysis
- Apply simple preference learning from follow-up feedback after sleep actions

## Supported Intents

Direct intents accepted by `/agent/run`:

- `fan_on`
- `fan_off`
- `enter_room`
- `sleep_mode`
- `focus_start`
- `focus_end`
- `comfort_adjust`
- `no_action`

Additional natural-language intents routed by `/agent/chat`:

- `status`
- `analyze_bedroom`
- `decision_request`

## Endpoint Behavior

### `GET /health`

Simple liveness probe. Returns the configured mode and tool backend.

### `GET /readyz`

Readiness probe that checks:

- selected tool backend
- MQTT listener connection state
- LLM reachability when configured
- vision fallback path when `CAMERA_MODE=file`

The endpoint returns HTTP `503` when any required check fails.

### `POST /agent/run`

Executes a direct intent through:

1. runtime state build
2. deterministic orchestration
3. policy evaluation and cooldown handling
4. tool execution and verification
5. episode recording

Example:

```bash
curl -X POST http://127.0.0.1:9000/agent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "intent": "fan_on",
    "args": {},
    "state": {"guest_mode": false}
  }'
```

### `POST /agent/chat`

Routes natural language into one of three response modes:

- `mode="action"`: routed intent or `decision_request` selected and executed
- `mode="info"`: status explanation or bedroom analysis
- `mode="memory_update"`: preference feedback updated from conversational follow-up

Examples:

```bash
curl -X POST http://127.0.0.1:9000/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"text": "start focus mode"}'
```

```bash
curl -X POST http://127.0.0.1:9000/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"text": "What should happen now?"}'
```

```bash
curl -X POST http://127.0.0.1:9000/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"text": "That was too cold. Warmer next time."}'
```

## Memory Model

SQLite namespaces currently used include:

- `belief`: door and presence state
- `prefs`: user preferences such as sleep temperature or light preference
- `episodes`: last episode, recent episodes, rolling summary
- `decision`: last chosen intent and trace for explainability
- `status`: last status response
- `vision`: latest bedroom analysis

Key memory components:

- `TieredMemory`: fetches relevant preferences, recent episodes, and rolling summaries
- `PreferenceFeedback`: updates sleep preferences from short follow-up feedback
- `StatusService`: explains recent behavior using beliefs, events, device state, and saved decision traces

## Configuration

Settings are loaded from `.env` with `pydantic-settings`. The source of truth is [src/core/config.py](/home/rosurya/bedroom-agent/apps/bedroom-agent/src/core/config.py).

Important variables:

- `AGENT_MODE=shadow|active`
- `TOOL_BACKEND=local|http|ha`
- `HA_BASE_URL`, `HA_TOKEN`
- `LLM_BASE_URL`, `LLM_MODEL`, `OPENAI_API_KEY`
- `LLM_TIMEOUT_S`
- `LLM_DECISION_ENABLED`
- `LLM_DECISION_TIMEOUT_S`
- `LLM_DECISION_MIN_CONFIDENCE`
- `LLM_DECISION_USE_VISION`
- `LLM_DECISION_MAX_EVENTS`
- `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`
- `ENTRY_LIGHT_ENTITY_ID`, `BEDROOM_LAMP_ENTITY_ID`, `BEDROOM_FAN_ENTITY_ID`, `BEDROOM_AC_ENTITY_ID`
- `TEMP_SENSOR_ENTITY_ID`, `HUMIDITY_SENSOR_ENTITY_ID`
- `COMFORT_TRIGGER_TEMP_C`, `COMFORT_TRIGGER_HUMIDITY_PCT`
- `COMFORT_TARGET_TEMP_C`, `SLEEP_TARGET_TEMP_C`
- `CAMERA_MODE`, `CAMERA_ENTITY_ID`, `CAMERA_DEVICE`
- `VISION_ANALYSIS_ENABLED`, `VISION_FALLBACK_IMAGE_PATH`, `VISION_DEBUG_SAVE_DIR`
- `REQUEST_BUDGET_S`

## Local Development

Install and run:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
uvicorn src.app:app --host 0.0.0.0 --port 9000 --reload
```

Run the service against the mock HA backend:

```bash
TOOL_BACKEND=http \
HA_BASE_URL=http://127.0.0.1:8124 \
VISION_ANALYSIS_ENABLED=false \
uvicorn src.app:app --host 0.0.0.0 --port 9000 --reload
```

## Docker

Use the included compose file:

```bash
docker compose up --build
```

The container publishes port `9000`, stores SQLite state under `/data`, and uses `/readyz` as its healthcheck target.

## Persistence And Logs

- SQLite state defaults to `data/memory.sqlite`
- JSONL event logs default to `logs/events.jsonl`
- Vision debug captures are written to `data/debug` when enabled

## Verification

```bash
./.venv/bin/ruff check src tests
./.venv/bin/pytest tests -q
```
