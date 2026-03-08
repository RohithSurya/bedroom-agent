# Runbook

This runbook covers local development, Docker startup, Home Assistant wiring, and the main failure modes seen in this repo.

## Prerequisites

- Python 3.10+
- Docker and Docker Compose
- A reachable Home Assistant instance
- A reachable MQTT broker if you want occupancy-driven automations
- A reachable LLM backend:
  - Ollama for local models, or
  - Mistral API for hosted inference

Optional:

- A USB camera or other configured bedroom image source
- Wyoming/faster-whisper for local speech-to-text

## Local Development

```bash
cd apps/bedroom-agent
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
uvicorn src.app:app --host 0.0.0.0 --port 9000 --reload
```

Basic checks:

```bash
curl http://localhost:9000/health
curl -X POST http://localhost:9000/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"start focus mode","state":{"guest_mode":false}}'
```

## Docker Startup

### Agent

```bash
cd apps/bedroom-agent
docker compose up --build -d
```

Notes:

- The service exposes `9000:9000`
- `./data` is mounted to `/data`
- The compose file assumes Linux Docker support for `host.docker.internal`

### Wyoming / Speech-to-Text

```bash
cd wyoming
docker compose up -d
```

This compose file starts `faster-whisper` and `wyoming-piper`.

## Environment Setup

Edit `apps/bedroom-agent/.env`.

Minimum fields to verify:

- `AGENT_MODE`
- `TOOL_BACKEND`
- `HA_BASE_URL`
- `HA_TOKEN`
- `LLM_PROVIDER`
- `LLM_BASE_URL`
- `LLM_MODEL`
- `MQTT_HOST`
- `MQTT_PORT`
- `CAMERA_MODE`

Provider notes:

- `LLM_PROVIDER=ollama` uses `LLM_BASE_URL/api/generate`
- `LLM_PROVIDER=mistral` ignores `LLM_BASE_URL` for generation and requires `MISTRAL_API_KEY`

## Home Assistant Wiring

Files:

- `infra/home-automation/ha_config/configuration.yaml`
- `infra/home-automation/ha_config/automations.yaml`
- `infra/home-automation/ha_config/scripts.yaml`

Operational rules:

- If you edit `configuration.yaml`, restart Home Assistant
- If you edit only automations or scripts, reload them from Home Assistant or restart
- `rest_command.bedroom_agent_chat` should point to the actual IP or hostname of the running agent

Current HA flow:

1. Assist matches the catch-all conversation command in `automations.yaml`.
2. HA calls `rest_command.bedroom_agent_chat`.
3. The response is parsed in `automations.yaml`.
4. HA speaks a synthesized reply with `set_conversation_response`.

## Operational Checks

### Health

```bash
curl http://localhost:9000/health
```

Expected:

- `"ok": true`
- correct `mode`
- correct `backend`

### Direct action

```bash
curl -X POST http://localhost:9000/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"intent":"fan_on","args":{},"state":{"guest_mode":false}}'
```

### Natural language

```bash
curl -X POST http://localhost:9000/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"analyze bedroom","state":{"guest_mode":false}}'
```

### Logs and memory

JSONL log:

```bash
tail -f apps/bedroom-agent/logs/events.jsonl
```

SQLite event memory:

```bash
sqlite3 apps/bedroom-agent/data/memory.sqlite \
  'select type, ts from events order by ts desc limit 20;'
```

## Common Failure Modes

### Assist only says "Okay"

Usually this means Home Assistant handled the phrase locally or the automation failed to build a response.

Check:

1. `automations.yaml` still contains the catch-all `"{command}"` conversation trigger
2. `rest_command.bedroom_agent_chat` returns HTTP `200`
3. The response contains either `result.summary` or valid action metadata
4. Home Assistant has reloaded the changed automation

### `vision analysis is unavailable right now`

This means image capture succeeded but the model path failed or returned unusable output.

Check:

1. `VISION_ANALYSIS_ENABLED=true`
2. The image source is working for the configured `CAMERA_MODE`
3. The configured model actually supports image input
4. The LLM backend is reachable and not timing out

### Action denied

A deny usually comes from policy, not transport.

Common reasons:

- `guest_mode_on`
- `presence_required`
- `cooldown_active:*`
- room is already comfortable and `comfort_adjust` is converted to `no_action`

### No entry automation from MQTT

Check:

1. `MQTT_HOST`, `MQTT_PORT`, `Z2M_DOOR_TOPIC`, and `Z2M_PRESENCE_TOPIC`
2. The MQTT listener started successfully at app boot
3. Belief updates are appearing in SQLite events
4. Door and presence events arrive in the expected time window

### Home Assistant unreachable from Docker

The compose file uses `host.docker.internal`.

Check:

1. Docker supports `host-gateway` on the host
2. Home Assistant is listening on the expected port
3. `HA_BASE_URL` is correct both inside and outside the container

## Incident Triage

When something fails, inspect in this order:

1. `GET /health`
2. direct `curl` to `/agent/chat` or `/agent/run`
3. `apps/bedroom-agent/logs/events.jsonl`
4. SQLite `events` table
5. Home Assistant automation traces and logs
6. LLM backend reachability
7. MQTT topic activity

## Recovery Actions

- Restart the agent container after `.env` changes
- Restart Home Assistant after `configuration.yaml` changes
- Reload automations/scripts after YAML edits in those files
- Clear or inspect cooldown-related state by reviewing recent events rather than guessing
- Temporarily switch to `AGENT_MODE=shadow` if you need to observe decisions without side effects
