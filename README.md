# bedroom-agent

Edge-Native Autonomous Intelligence using LLM centric logic that control the entities of the room. 
Totally offline system deployed on Jetson Orin Nano Super

The project is split into three pieces:

- `apps/bedroom-agent`: the FastAPI service that routes requests, evaluates policy, executes tools, listens to MQTT, and stores memory/logs
- `infra/home-automation/ha_config`: Home Assistant configuration that exposes the agent to Assist and scripts
- `wyoming`: speech-to-text container configuration for a local Assist pipeline

## What It Does

- Accepts direct intent requests such as `fan_on`, `sleep_mode`, or `focus_start`
- Accepts natural language requests through `/agent/chat`
- Uses deterministic orchestration for safety-critical actions
- Uses SQLite for beliefs, preferences, recent room analysis, and event memory
- Writes append-only JSONL logs for replay and debugging
- Supports room analysis from a bedroom camera snapshot
- Integrates with Home Assistant for voice, climate, fan, lights, and TTS


## Hardware + ecosystem (locked)

**Compute**

- **NVIDIA Jetson Orin Nano 8GB**

**Sensors + control**

- **Camera** -> to feed photos to mistral model
- **mmWave presence sensor** (Zigbee) → presence/occupancy belief state
- **Zigbee smart plug** → bedside lamp + power telemetry use-cases
- **Home Assistant Connect ZBT-2** (Zigbee coordinator, run in **Zigbee mode** for v1.0)
- **Broadlink RM4 Mini (IR blaster)** → controls **Vissani window AC**
- **Home Assistant + SmartIR climate entity** → reliable HVAC abstraction
- **HomePod Gen2** → TTS output + temp/humidity sensor (as available in Home ecosystem)

**Voice control path (locked Option 1)**

- **HomePod Siri → Apple Home Scene → Home Assistant → LLM/Agent → HomePod speaks**


## Architecture

At runtime the agent looks like this:

1. Home Assistant or a caller sends `POST /agent/run` or `POST /agent/chat`.
2. The app builds a runtime state from Home Assistant entities plus stored beliefs and preferences.
3. `NLRouter` maps text to a high-level intent.
4. `Orchestrator` converts the intent into deterministic tool calls after policy checks.
5. `Runner` executes the tool calls, verifies state changes, applies cooldowns, and logs results.
6. MQTT listeners update occupancy and door beliefs continuously in the background.
7. Optional vision analysis captures a bedroom image and asks the configured LLM/VLM for structured output.

Mermaid diagrams live in `docs/`:

- `docs/architecture.mmd`
- `docs/data_architecture.mmd`
- `docs/analyze_bedroom.mmd`
- `docs/night_mode.mmd`
- `docs/habit_engine.mmd`

## Quick Start

### Local Python

```bash
cd apps/bedroom-agent
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
uvicorn src.app:app --host 0.0.0.0 --port 9000 --reload
```

### Docker

```bash
cd apps/bedroom-agent
docker compose up --build
```

The service listens on `http://localhost:9000`.

## Core Configuration

The agent reads settings from `apps/bedroom-agent/.env`. The most important variables are:

- `AGENT_MODE`: `shadow` or `active`
- `TOOL_BACKEND`: `local`, `http`, or `ha`
- `HA_BASE_URL` and `HA_TOKEN`: Home Assistant API access
- `LLM_PROVIDER`: `ollama` or `mistral`
- `LLM_BASE_URL` and `LLM_MODEL`: local model backend settings
- `MISTRAL_API_KEY`: required when `LLM_PROVIDER=mistral`
- `MQTT_HOST`, `MQTT_PORT`, `Z2M_DOOR_TOPIC`, `Z2M_PRESENCE_TOPIC`: Zigbee2MQTT integration
- `CAMERA_MODE`, `CAMERA_DEVICE`, `VISION_FALLBACK_IMAGE_PATH`: image capture configuration

For a full list, see `apps/bedroom-agent/src/core/config.py`.

## Home Assistant Integration

Home Assistant configuration lives under `infra/home-automation/ha_config`.

- `configuration.yaml` defines `rest_command` calls into the agent
- `automations.yaml` maps Assist conversation phrases to `/agent/chat`
- `scripts.yaml` exposes helper scripts such as `agent_chat_request` and `bedroom_agent_speak`

The current voice entrypoint supports phrases such as:

- `Tony start focus mode`
- `bedroom agent cool the room`
- `analyze bedroom`
- `check my bedroom`

If you change `configuration.yaml`, restart Home Assistant. If you only change automations or scripts, a reload is usually enough.

## Repository Layout

```text
apps/bedroom-agent/             FastAPI agent service, Dockerfile, tests
infra/home-automation/ha_config Home Assistant YAML config
wyoming/                        faster-whisper compose file
docs/                           diagrams, contracts, runbook
```

## Documentation

- `apps/bedroom-agent/README.md`: app-specific setup and API usage
- `docs/contracts.md`: request/response, tool, state, and event contracts
- `docs/runbook.md`: deployment, operations, and troubleshooting

## Current Scope

This repo is still a pragmatic v0:

- orchestration is deterministic even when routing uses an LLM
- safety checks are local and explicit
- voice support is implemented through Home Assistant conversation automations
- room analysis is useful, but depends heavily on the configured model actually supporting image inputs well
