# EdgeAgent (aka bedroom-agent)

EdgeAgent (aka bedroom-agent) is a local-first embodied AI system for personalized room intelligence—combining multimodal perception, memory, and policy-gated action to safely control a real environment on edge hardware.
Deployed on Jetson nano super.

**Totally offline system deployed on [Jetson Orin Nano Super](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-orin/nano-super-developer-kit/)**

The project is split into four pieces:

- `apps/bedroom-agent`: the FastAPI service that routes requests, evaluates policy, executes tools, listens to MQTT, and stores memory/logs

- `infra/home-automation/ha_config`: Home Assistant configuration that exposes the agent to Assist and scripts

- `wyoming`: speech-to-text using fast-whisper. Text to speech using piper.

- `Ministral 3B Instruct`: Quantized LLM running using llama.cpp (CUDA optimized)


**LLM Stack**

- This project uses [Ministral 3B Instruct (2512)](https://docs.mistral.ai/models/ministral-3-3b-25-12) as the core LLM.

- Because the system runs on a Jetson Orin Nano Super with 8GB memory, the model is deployed in a [4-bit quantized GGUF](https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512-GGUF) format to fit within edge-device resource limits.

- The model is served locally using llama.cpp as a system service, with CUDA acceleration enabled on the Jetson for faster inference.

- To stay within the device’s memory and latency constraints, the model runs with a reduced context window of 2048 tokens and uses GPU layer offloading (`-ngl`) so computation is shared across the CPU and GPU.

**Optimization Notes**
- TensorRT-based optimization was explored to further improve performance and quantization efficiency.

- However, because this project uses a multimodal vision + text workflow, TensorRT would require handling the vision and language components separately.

- On an 8GB edge device, that split pipeline introduced too much complexity and was not practical for the current system design.

- For that reason, llama.cpp with CUDA offloading was chosen as the most feasible and reliable deployment path for this version of the project.


**Services running for the project**
- Bedroom-agent microservice (Brain of the project)
- [ministral-3b-2512](https://docs.mistral.ai/models/ministral-3-3b-25-12) LLM model
- Zigbee2MQTT
- Mosquitto (Message queue)
- Home assistant
- Wyoming (For speech to text)
- Piper (For text to speech)


**Hardware + ecosystem**

**Compute**

- **NVIDIA Jetson Orin Nano 8GB**

**Sensors + control**

- **Camera** -> to feed photos to the local Ministral model backend
- **mmWave presence sensor** (Zigbee) → presence/occupancy belief state
- **Zigbee smart plug** → bedside lamp + power telemetry use-cases
- **Home Assistant Connect ZBT-2** (Zigbee coordinator, run in **Zigbee mode** for v1.0)
- **Broadlink RM4 Mini (IR blaster)** → controls **Vissani window AC**
- **Home Assistant + SmartIR climate entity** → reliable HVAC abstraction
- **HomePod Gen2** → TTS output + temp/humidity sensor (as available in Home ecosystem)
- **Door Sensor** → For tracking door in and out events and also used for presence tracking
- **Temperature and Humidity Sensor** → Monitors the temperature and humidity of the room. Adjusting comfort mode based on temperature and humidity.
- **Switch bot** → Switch bot to control dome light in the room. Making a dumb light into a smart one.

**Voice control path (locked Option 1)**

- **HomePod Siri → Apple Home Scene → Home Assistant → LLM/Agent → HomePod speaks**
- **Homeassistant assist** → Works with voice assist with Tony open wake word (Using apple shortcuts). Relays commands to `/agent/chat` making it a seamless experience.

## Visual Overview

### Architecture Diagram

High-level system map:

```mermaid
flowchart LR
  User["User"]

  subgraph Voice["Voice + Home Assistant"]
    Assist["Assist / conversation trigger"]
    Automation["HA automations + scripts"]
    Rest["rest_command\nbedroom_agent_chat / bedroom_agent_run"]
    HAApi["Home Assistant service + state API"]
    Entities["HA entities\nlight fan climate sensors"]
    Speak["script.bedroom_agent_speak"]
  end

  subgraph Agent["bedroom-agent FastAPI service"]
    API["FastAPI app\n/health /agent/run /agent/chat"]
    State["Runtime state builder"]
    Router["NLRouter"]
    Status["StatusService"]
    Decision["DecisionEngine"]
    Vision["BedroomRoomAnalyzer"]
    Orchestrator["Orchestrator"]
    Factory["ActionFactory\nLightAction FanAction\nSpeechAction ClimatePlan"]
    Plan["ToolCall plan\nstable executor boundary"]
    Runner["Runner"]
    Registry["ToolBehaviorRegistry"]
    Behavior["Tool behaviors\nLightSet FanSet SwitchSet\nClimateSetMode Temperature FanMode\nTtsSay Default"]
    Tools["HA tool client\nreal / http / local"]
    MQTT["Z2MMqttListener"]
  end

  subgraph Storage["Local state + logs"]
    SQLite["SQLite memory\nbelief / prefs / vision / status / events"]
    Jsonl["JSONL log\nlogs/events.jsonl"]
  end

  subgraph Inputs["External inputs"]
    Broker["MQTT broker / Zigbee2MQTT"]
    Camera["BedroomImageSource\nfswebcam / HA snapshot / file"]
    Model["LLM provider\nOllama or Mistral API"]
    Whisper["Wyoming / faster-whisper"]
    Piper["Wyoming / piper-tts"]
  end

  User --> Assist
  Whisper -. speech-to-text .-> Assist
  Assist --> Automation
  Automation --> Rest
  Rest --> API

  API --> State
  State --> Tools
  Tools <--> HAApi
  HAApi <--> Entities

  API --> Router
  API --> Status
  API --> Decision
  API --> Vision
  API --> Orchestrator
  Orchestrator --> Factory
  Factory --> Plan
  Plan --> Runner

  Router <--> Model
  Status <--> Model
  Decision <--> Model
  Vision --> Camera
  Vision <--> Model

  Runner --> Registry
  Registry --> Behavior
  Behavior --> Tools
  Runner --> Jsonl

  Broker --> MQTT
  MQTT --> SQLite

  State <--> SQLite
  Status --> SQLite
  Decision --> SQLite
  Vision --> SQLite
  API --> SQLite
  HAApi --> Speak
  Speak -. text-to-speech .-> Piper
  Piper -. spoken response .-> User
```

Door/presence Zigbee sensor flow is broken out separately in the sensor diagram below.

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

### Data Architecture

```mermaid
flowchart TD
  subgraph Inputs["Input streams"]
    Voice["Voice, webhook, or HTTP request"]
    EntityReads["Live Home Assistant entity reads\nlight climate fan temp humidity"]
    MqttEvents["MQTT door / presence / distance payloads"]
    Images["Bedroom snapshots\ncamera device, HA snapshot, or file"]
  end

  subgraph Memory["SQLite data/memory.sqlite"]
    Belief["belief namespace\npresence door_open target_distance\nlast_door_open_ts last_enter_trigger_ts\nlast_presence_false_ts"]
    Prefs["prefs namespace\nguest_mode\nsleep.* focus.* comfort.*"]
    VisionState["vision namespace\nlatest_bedroom_analysis"]
    DecisionState["decision namespace\nlast_choice last_trace"]
    Episodes["episodes namespace\nlast recent rolling_summary"]
    StatusState["status namespace\nlast_summary"]
    Events["events table\nappend-only typed events"]
  end

  subgraph Runtime["Runtime context"]
    Tiered["TieredMemory\nrelevant prefs + episode summary"]
    StatePacket["build_runtime_state()\nstate packet for routing and policy"]
    Services["NLRouter / StatusService /\nDecisionEngine / Orchestrator"]
    VisionSvc["BedroomRoomAnalyzer"]
    Feedback["PreferenceFeedback"]
    EpisodesWriter["record_episode()"]
  end

  Logs["logs/events.jsonl\ncorrelation-based execution log"]

  MqttEvents --> Belief
  MqttEvents --> Events
  Images --> VisionSvc
  VisionSvc --> VisionState
  VisionSvc --> Events

  EntityReads --> StatePacket
  Voice --> StatePacket
  Belief --> StatePacket
  Prefs --> Tiered
  Episodes --> Tiered
  Tiered --> StatePacket
  VisionState --> StatePacket

  StatePacket --> Services
  Voice --> Services
  Belief --> Services
  Prefs --> Services
  DecisionState --> Services
  Episodes --> Services
  Events --> Services

  Services --> VisionSvc
  Services --> Feedback
  Services --> DecisionState
  Services --> StatusState
  Services --> Events
  Services --> Logs
  Services --> EpisodesWriter

  Feedback --> Prefs
  Feedback --> Events
  EpisodesWriter --> Episodes
  EpisodesWriter --> Events
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
  `Z2M_DOOR_TOPIC` can be a comma-separated list when multiple door sensors should share the same entry logic.
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

Service-specific docs are in [apps/bedroom-agent/README.md](/apps/bedroom-agent/README.md).
