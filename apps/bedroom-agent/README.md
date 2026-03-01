# bedroom-agent service

FastAPI service for the bedroom automation agent.

This package is the runtime core of the repo. It exposes HTTP endpoints for direct intents and natural-language requests, consumes Home Assistant and MQTT state, and executes deterministic tool calls after policy evaluation.

## Responsibilities

- Serve `GET /health`, `POST /agent/run`, and `POST /agent/chat`
- Build runtime context from Home Assistant entity state and local memory
- Route natural language to high-level intents
- Enforce policy gates and cooldowns
- Execute Home Assistant tools and verify results
- Listen for Zigbee2MQTT door and presence events
- Persist beliefs, preferences, recent analysis, and event history
- Optionally call an LLM for routing, status answers, decision support, and bedroom image analysis

## Supported Intents

Direct execution intents accepted by `/agent/run`:

- `night_mode`
- `fan_on`
- `fan_off`
- `enter_room`
- `sleep_mode`
- `focus_start`
- `focus_end`
- `comfort_adjust`
- `no_action`

Natural-language routing through `/agent/chat` can additionally return:

- `analyze_bedroom`
- `status`
- `decision_request`

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
uvicorn src.app:app --host 0.0.0.0 --port 9000 --reload
```

Health check:

```bash
curl http://localhost:9000/health
```

Example direct intent:

```bash
curl -X POST http://localhost:9000/agent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "intent": "fan_on",
    "args": {},
    "state": {"guest_mode": false}
  }'
```

Example natural-language request:

```bash
curl -X POST http://localhost:9000/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "start focus mode",
    "state": {"guest_mode": false}
  }'
```

## Docker

```bash
docker compose up --build
```

The container publishes port `9000` and mounts `./data` to `/data` for SQLite state and fallback images.

## Configuration

Settings are loaded from `.env` using `pydantic-settings`.

Important variables:

- `AGENT_MODE=shadow|active`
- `TOOL_BACKEND=local|http|ha`
- `HA_BASE_URL`
- `HA_TOKEN`
- `LLM_PROVIDER=ollama|mistral`
- `LLM_BASE_URL`
- `LLM_MODEL`
- `MISTRAL_API_KEY`
- `MQTT_HOST`
- `MQTT_PORT`
- `CAMERA_MODE=device|ha_snapshot|file`
- `VISION_ANALYSIS_ENABLED=true|false`

The authoritative settings list is in `src/core/config.py`.

## Persistence and Logs

- SQLite state: `data/memory.sqlite` by default
- JSONL event log: `logs/events.jsonl` by default

SQLite namespaces currently used include:

- `belief`
- `prefs`
- `vision`
- `status`

## Model Providers

The current code supports:

- `ollama`: local `/api/generate` backend, including structured output and image payloads
- `mistral`: hosted Mistral Chat Completions API

Routing, status responses, decision selection, and room analysis all use the provider built by `src/llm/factory.py`.

## Notes on Safety

- The LLM does not emit raw Home Assistant service calls.
- Tool execution remains deterministic in `Orchestrator` and `Runner`.
- Policy checks cover guest mode, presence, cooldowns, and comfort-related guardrails.
- When the LLM is unavailable, the app falls back to deterministic behavior or concise failure summaries.
