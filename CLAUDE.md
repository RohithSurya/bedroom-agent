# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Bedroom-Agent** is a local-first embodied AI system for bedroom automation running on an NVIDIA Jetson Orin Nano (8GB). It combines a quantized LLM (Ministral 3B via llama.cpp), MQTT-based sensor integration, and a Home Assistant backend to enable voice-controlled and sensor-driven room automation — entirely offline.

## Development Commands

All commands run from `apps/bedroom-agent/`:

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -U pip && pip install -e ".[dev]"

# Run locally (no HA or vision required)
TOOL_BACKEND=local VISION_ANALYSIS_ENABLED=false \
  uvicorn src.app:app --host 0.0.0.0 --port 9000 --reload

# Lint
./.venv/bin/ruff check src tests

# Test (all)
./.venv/bin/pytest tests -q

# Test (single file)
./.venv/bin/pytest tests/test_nl_router.py -q
```

Docker deployment:
```bash
cd apps/bedroom-agent && docker compose up --build -d
```

## Architecture

The agent follows a **three-tier pipeline** for every request:

```
Input (HTTP /agent/run or /agent/chat)
  → NLRouter (text → intent)
  → Orchestrator (intent → action plan, policy gates, cooldowns)
  → Runner (execute tool calls with retry + circuit breaker)
  → SQLite (persist beliefs, preferences, episode logs)
```

### Key Components

**`src/agent/`**
- `nl_router.py` — Maps natural language to intents via pattern matching, falls back to LLM
- `orchestrator.py` — Deterministic planner: converts intents to `ToolCall[]` lists, applies policies and cooldowns
- `policies.py` — Safety gates (guest_mode, presence_required, per-intent cooldowns)
- `runner.py` — Executes tool plans; handles retry, circuit breaker, deadline budget, and verification reads
- `decision_engine.py` — LLM-based decision making for ambiguous requests
- `mqtt_listener.py` — MQTT subscriber for door/presence sensor events; drives entry/vacancy automation

**`src/memory/`**
- `sqlite_kv.py` — Namespaced SQLite KV store (namespaces: `belief`, `prefs`, `vision`, `decision`, `status`, `episodes`)
- `tiered_memory.py` — Aggregates preferences and episode summaries for LLM context

**`src/tools/`**
- `tool_executor.py` — Local mock backend (used in dev/tests)
- `ha_http_client.py` — REST backend for Home Assistant
- `ha_real_client.py` — WebSocket HA client

**`src/core/config.py`** — All configuration via Pydantic settings (~80 fields). Critical ones:
- `AGENT_MODE`: `shadow` (log-only) or `active` (executes actions)
- `TOOL_BACKEND`: `local`, `http`, or `ha`
- `LLM_DECISION_ENABLED`: enables LLM routing fallback
- `ENTRY_WINDOW_S`, `ENTRY_COOLDOWN_S`, `VACANCY_OFF_DELAY_S`: timing for sensor-driven automation

**`src/reliability/`** — Circuit breaker, retry, deadline budget, timeout — used by `runner.py`

### Intents

Defined in `agent/intent_registry.py`: `fan_on`, `fan_off`, `enter_room`, `sleep_mode`, `focus_start`, `focus_end`, `comfort_adjust`, `no_action`, `analyze_bedroom`, `decision_request`

### Data Flow for Sensor Events

MQTT (Zigbee2MQTT) → `MqttListener` → updates `belief` namespace in SQLite → triggers `enter_room` intent if door open + presence within `ENTRY_WINDOW_S` → Orchestrator → Runner → HA

### Services & Ports

| Service | Port |
|---|---|
| bedroom-agent (FastAPI) | 9000 |
| llama.cpp (Ministral 3B) | 8081 |
| Home Assistant | 8123 |
| Mosquitto (MQTT) | 1883 |

The LLM service is managed by systemd (`llm.service`) and configured in `llama-server-config/`.

## Infra Layout

- `infra/home-automation/` — Home Assistant config (automations, scripts, scenes, Zigbee2MQTT)
- `infra/jetson/` — Jetson Orin setup scripts
- `wyoming/` — faster-whisper (STT) + piper-tts (TTS) docker services
- `mock_ha/` — Mock Home Assistant for integration testing
- `evals/` — Evaluation harnesses
- `docs/` — Runbook, API contracts, architecture diagrams
