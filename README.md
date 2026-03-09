# bedroom-agent

Local-first bedroom automation stack centered on a FastAPI agent. The agent keeps tool execution deterministic, uses lightweight SQLite memory, listens to MQTT occupancy signals, and can optionally call an OpenAI-compatible LLM for routing, explanation, decision support, and bedroom image analysis.

## Visual Overview

### Architecture Diagram

High-level system map:

```mermaid
flowchart LR
    subgraph Inputs[User Inputs and Room Signals]
        User[User or API client]
        Voice[Voice webhook or chat UI]
        Door[Zigbee door sensor]
        Presence[Zigbee mmWave presence sensor]
        Camera[Bedroom camera or fallback image]
    end

    subgraph SensorInfra[Sensor Transport]
        Hub[Zigbee hub or coordinator]
        Z2M[Zigbee2MQTT]
        Broker[MQTT broker]
    end

    subgraph App[bedroom-agent FastAPI app]
        API[HTTP endpoints]
        MQTTListener[MQTT listener and automation callbacks]
        Runtime[Routing, policy, and execution]
        Vision[Bedroom vision analysis]
        Memory[SQLite memory and JSONL logs]
    end

    subgraph Integrations[Integrations]
        LLM[OpenAI-compatible LLM]
        Tools[Tool backend]
        HA[Home Assistant devices]
    end

    User --> API
    Voice --> API
    Door --> Hub
    Presence --> Hub
    Hub --> Z2M
    Z2M --> Broker
    Broker --> MQTTListener
    MQTTListener --> Runtime
    MQTTListener --> Memory
    Camera --> Vision
    API --> Runtime
    Runtime <--> Memory
    Runtime --> Vision
    Runtime --> LLM
    Vision --> LLM
    Runtime --> Tools
    Tools --> HA
```

Very detailed app component view:

```mermaid
flowchart LR
    subgraph Entrypoints[Entrypoints]
        Health["GET /health"]
        Readyz["GET /readyz"]
        Run["POST /agent/run"]
        Chat["POST /agent/chat"]
        Life["FastAPI lifespan startup and shutdown"]
    end

    subgraph Bootstrap[Bootstrap and Wiring]
        Settings["Settings from .env and pydantic-settings"]
        AgentState["AgentAppState"]
        BuildLLM["llm.factory.build_llm_client"]
        BuildExec["_build_executor"]
    end

    subgraph StatefulCore[Runtime State Assembly]
        BuildState["build_runtime_state"]
        NeedIds["_required_entity_ids_for_intent"]
        ReadHA["Runner.read_entity_state"]
        VisionState["_build_vision_state"]
        Tiered["TieredMemory"]
        PrefDefaults["default and learned preferences"]
        Recent["recent episodes and rolling summary"]
    end

    subgraph Routing[Routing and Interpretation]
        Router["NLRouter"]
        Decision["DecisionEngine"]
        Status["StatusService"]
        Vision["BedroomRoomAnalyzer"]
        Feedback["PreferenceFeedback"]
    end

    subgraph Planning[Planning and Policy]
        Orch["Orchestrator"]
        Policies["agent.policies evaluators"]
        ActionFactory["ActionFactory"]
        ToolCalls["ToolCall plans with correlation and idempotency ids"]
        Cooldowns["CooldownStore"]
    end

    subgraph Execution[Execution and Verification]
        Runner["Runner.execute_actions"]
        Behaviors["ToolBehaviorRegistry"]
        Retry["RetryPolicy"]
        Breaker["CircuitBreaker"]
        Deadline["Deadline"]
        Logs["JsonlLogger"]
    end

    subgraph Storage[Local Persistence]
        KV["SqliteKV"]
        Belief["belief namespace"]
        Prefs["prefs namespace"]
        Episodes["episodes namespace"]
        DecisionNS["decision namespace"]
        StatusNS["status namespace"]
        VisionNS["vision namespace"]
    end

    subgraph Integrations[External and Device Integrations]
        MQTT["Z2MMqttListener"]
        Broker["MQTT broker"]
        Z2M["Zigbee2MQTT bridge"]
        Hub["Zigbee coordinator or hub"]
        Img["BedroomImageSource"]
        LLM["OpenAIClient or compatible endpoint"]
        LocalExec["ToolExecutor local backend"]
        HTTPExec["HAToolClientHTTP"]
        RealExec["HAToolClientReal"]
        HA["Home Assistant entities and services"]
        Camera["camera proxy, device snapshot, or file fallback"]
    end

    subgraph Room[Physical Room Devices]
        DoorSensor["bedroom door sensor"]
        PresenceSensor["mmWave presence sensor"]
        LightDevice["bedroom or entry light"]
        FanDevice["bedroom fan"]
        ACDevice["bedroom AC or climate"]
        Speaker["TTS speaker"]
    end

    Life --> Settings
    Settings --> AgentState
    BuildLLM --> AgentState
    BuildExec --> AgentState
    Life --> MQTT
    DoorSensor --> Hub
    PresenceSensor --> Hub
    Hub --> Z2M
    Z2M --> Broker
    Broker --> MQTT
    MQTT --> KV

    Health --> AgentState
    Readyz --> AgentState
    Run --> BuildState
    Chat --> Router
    Chat --> BuildState

    AgentState --> Router
    AgentState --> Decision
    AgentState --> Status
    AgentState --> Vision
    AgentState --> Feedback
    AgentState --> Orch
    AgentState --> Runner
    AgentState --> KV
    AgentState --> Tiered
    AgentState --> Cooldowns
    AgentState --> Logs

    BuildState --> NeedIds
    NeedIds --> ReadHA
    ReadHA --> LocalExec
    ReadHA --> HTTPExec
    ReadHA --> RealExec
    BuildState --> Tiered
    BuildState --> VisionState
    Tiered --> PrefDefaults
    Tiered --> Recent
    Tiered --> KV
    VisionState --> KV
    BuildState --> KV

    Router --> Decision
    Router --> Status
    Router --> Vision
    Router --> Feedback
    Router --> Orch
    Router --> LLM
    Decision --> LLM
    Decision --> KV
    Status --> LLM
    Status --> KV
    Feedback --> KV
    Vision --> Img
    Vision --> LLM
    Vision --> KV
    Img --> Camera

    Run --> Orch
    BuildState --> Orch
    Decision --> Orch
    Orch --> Policies
    Orch --> ActionFactory
    Policies --> Cooldowns
    Orch --> ToolCalls
    Orch --> Runner

    Runner --> Behaviors
    Runner --> Retry
    Runner --> Breaker
    Runner --> Deadline
    Runner --> Logs
    Runner --> Cooldowns
    ToolCalls --> Runner

    Runner --> LocalExec
    Runner --> HTTPExec
    Runner --> RealExec
    LocalExec --> HA
    HTTPExec --> HA
    RealExec --> HA
    HA --> LightDevice
    HA --> FanDevice
    HA --> ACDevice
    HA --> Speaker

    KV --> Belief
    KV --> Prefs
    KV --> Episodes
    KV --> DecisionNS
    KV --> StatusNS
    KV --> VisionNS
```

### Sample Bedroom Image

Tracked sample snapshot used for local bedroom-analysis development:

![Sample bedroom snapshot](apps/bedroom-agent/data/bedroom-latest.jpg)

## Repository Layout

- `apps/bedroom-agent`: main FastAPI service, agent logic, memory, tests, Docker config
- `infra/home-automation`: Home Assistant and related deployment assets
- `mock_ha`: lightweight mock Home Assistant service for local integration work
- `wyoming`: local speech-to-text container config
- `evals`: evaluation scenarios and harnesses

## Current Behavior

- Direct action endpoint: `POST /agent/run`
- Natural-language endpoint: `POST /agent/chat`
- Readiness and liveness: `GET /health`, `GET /readyz`
- Deterministic orchestration for `fan_on`, `fan_off`, `enter_room`, `sleep_mode`, `focus_start`, `focus_end`, `comfort_adjust`, and `no_action`
- Natural-language routing for `status`, `analyze_bedroom`, and `decision_request`
- SQLite-backed beliefs, preferences, decision traces, recent episodes, and cached vision analysis
- Sleep preference feedback from follow-up chat such as "too cold" or "warmer next time"
- Optional bedroom snapshot analysis using a local or remote OpenAI-compatible model endpoint

## Flow Diagrams

### Sensor and Occupancy Flow

```mermaid
flowchart LR
    Door["Zigbee door sensor"] --> Hub["Zigbee coordinator or hub"]
    Presence["Zigbee mmWave sensor"] --> Hub
    Hub --> Z2M["Zigbee2MQTT"]
    Z2M --> Broker["MQTT broker"]
    Broker --> Listener["Z2MMqttListener"]

    Listener --> DoorParse["parse door payload"]
    Listener --> PresenceParse["parse presence and distance payload"]
    DoorParse --> Belief["SqliteKV belief namespace"]
    PresenceParse --> Belief

    PresenceParse --> EntryCheck["entry-window and cooldown logic"]
    DoorParse --> EntryCheck
    EntryCheck --> EnterCB["_on_enter_room callback"]
    EnterCB --> EnterOrch["Orchestrator intent enter_room"]
    EnterOrch --> Runner["Runner.execute_actions"]
    Runner --> LightOn["light.set on"]

    PresenceParse --> VacancyTimer["vacancy timer scheduling"]
    VacancyTimer --> VacancyCheck["_on_room_vacant callback"]
    VacancyCheck --> LightOff["light.set off if room stays vacant"]

    Belief --> Runtime["future build_runtime_state reads"]
    Runner --> Logs["JsonlLogger and event log"]
```

### Sleep Mode Sequence

```mermaid
sequenceDiagram
    participant User
    participant API as FastAPI /agent/run
    participant State as AgentAppState
    participant Memory as SqliteKV + TieredMemory
    participant Orch as Orchestrator
    participant Policy as sleep policy + cooldowns
    participant Runner
    participant Tools as ToolExecutor or HA client
    participant HA as Home Assistant devices
    participant Logs as JsonlLogger

    User->>API: POST /agent/run {"intent":"sleep_mode"}
    API->>State: build_runtime_state(intent="sleep_mode")
    State->>Memory: load beliefs, prefs, recent episodes
    State->>Runner: read_entity_state(light, AC, temp, humidity)
    Runner->>Tools: read current entity states
    Tools-->>Runner: HA-style state payloads
    Runner-->>State: light and climate context
    State-->>API: runtime state

    API->>Orch: handle_request("sleep_mode", state)
    Orch->>Policy: evaluate_sleep_mode(state)
    Policy-->>Orch: allow or deny + 120s cooldown
    Orch->>Policy: apply cooldown key

    alt policy allows request
        Orch->>Orch: build tool plan
        Note over Orch: light off if needed
        Note over Orch: climate cool if room is warm
        Note over Orch: else fan_only or fan fallback
        Orch-->>API: tool calls + decision
        API->>Runner: execute_actions(plan)

        loop each tool call
            Runner->>Tools: execute(call)
            Tools->>HA: service invocation
            HA-->>Tools: result
            Tools-->>Runner: ToolResult
            Runner->>Logs: write tool_result and verification
        end

        opt successful active-mode run
            Runner->>Policy: mark cooldown as used
        end

        API->>Memory: record episode, decision, and execution
        API-->>User: decision + actions + execution
    else policy denies or cooldown blocks
        Orch-->>API: blocked plan
        API->>Runner: execute_actions(blocked plan)
        Runner->>Logs: record denial outcome
        API->>Memory: record blocked episode
        API-->>User: denied decision response
    end
```

### Direct Intent Execution

```mermaid
flowchart LR
    A[POST /agent/run] --> B[Build runtime state]
    B --> C[Orchestrator selects action plan]
    C --> D[Policy gates and cooldown checks]
    D --> E[Runner executes tool actions]
    E --> F[Tool backend or Home Assistant]
    E --> G[JSONL logs and SQLite episode memory]
    F --> H[API response with action results]
```

### Natural-Language Routing

```mermaid
flowchart TD
    A[POST /agent/chat] --> B[Router classifies request]
    B --> C{Intent family}
    C -->|Direct action| D[Map to action intent]
    C -->|Status| E[Status service]
    C -->|Bedroom analysis| F[Room analyzer]
    C -->|Decision request| G[Decision engine]
    C -->|Preference feedback| H[Preference updater]
    D --> I[Orchestrator plus Runner]
    G --> I
    E --> J[mode = info]
    F --> J
    H --> K[mode = memory_update]
    I --> L[mode = action]
```

### Bedroom Vision Analysis

```mermaid
flowchart LR
    A[Analyze bedroom request] --> B[Image source fetches snapshot]
    B --> C{Image available}
    C -->|No| D[Return failure summary]
    C -->|Yes| E[Room analyzer prompt build]
    E --> F[Optional LLM vision call]
    F --> G[Normalize structured output]
    G --> H[Persist latest vision result in SQLite]
    H --> I[Return summary or exact answer]
```

## Quick Start

Install the service:

```bash
cd apps/bedroom-agent
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

Run the agent in fully local mode:

```bash
TOOL_BACKEND=local \
VISION_ANALYSIS_ENABLED=false \
uvicorn src.app:app --host 0.0.0.0 --port 9000 --reload
```

Optional: run the mock Home Assistant service from the repo root:

```bash
python -m uvicorn mock_ha.app:app --host 0.0.0.0 --port 8124 --reload
```

Then point the agent at it:

```bash
cd apps/bedroom-agent
TOOL_BACKEND=http \
HA_BASE_URL=http://127.0.0.1:8124 \
VISION_ANALYSIS_ENABLED=false \
uvicorn src.app:app --host 0.0.0.0 --port 9000 --reload
```

## API At A Glance

Health check:

```bash
curl http://127.0.0.1:9000/health
curl http://127.0.0.1:9000/readyz
```

Direct intent:

```bash
curl -X POST http://127.0.0.1:9000/agent/run \
  -H 'Content-Type: application/json' \
  -d '{
    "intent": "sleep_mode",
    "args": {},
    "state": {"guest_mode": false}
  }'
```

Natural-language request:

```bash
curl -X POST http://127.0.0.1:9000/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "text": "What should happen now?",
    "state": {"guest_mode": false}
  }'
```

`/agent/chat` can return:

- `mode="action"` for routed or decision-driven actions
- `mode="info"` for status or bedroom analysis queries
- `mode="memory_update"` when follow-up feedback updates stored preferences

## Configuration

The authoritative settings live in [apps/bedroom-agent/src/core/config.py](/home/rosurya/bedroom-agent/apps/bedroom-agent/src/core/config.py).

Common variables:

- `AGENT_MODE=shadow|active`
- `TOOL_BACKEND=local|http|ha`
- `HA_BASE_URL`, `HA_TOKEN`
- `LLM_BASE_URL`, `LLM_MODEL`, `OPENAI_API_KEY`
- `LLM_DECISION_ENABLED`, `LLM_DECISION_TIMEOUT_S`, `LLM_DECISION_MIN_CONFIDENCE`
- `MQTT_HOST`, `MQTT_PORT`, `Z2M_DOOR_TOPIC`, `Z2M_PRESENCE_TOPIC`
- `SQLITE_PATH`
- `CAMERA_MODE=device|ha_snapshot|file`
- `VISION_ANALYSIS_ENABLED`, `VISION_FALLBACK_IMAGE_PATH`

## Verification

From `apps/bedroom-agent`:

```bash
./.venv/bin/ruff check src tests
./.venv/bin/pytest tests -q
```

## More Detail

Service-specific docs are in [apps/bedroom-agent/README.md](/home/rosurya/bedroom-agent/apps/bedroom-agent/README.md).
