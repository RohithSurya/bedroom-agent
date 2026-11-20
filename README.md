# EdgeAgent (bedroom-agent)

[![ci](https://github.com/RohithSurya/bedroom-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/RohithSurya/bedroom-agent/actions/workflows/ci.yml)
![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)

**An offline, multimodal AI agent that runs a real bedroom on an 8 GB NVIDIA Jetson Orin Nano Super.** It turns voice, sensors and a camera into lights, fan, AC and spoken replies through Home Assistant, with no cloud calls.

🏆 **1st Prize, Mistral AI Worldwide Hackathon NYC + NVIDIA On-Device Track** (Feb 28 – Mar 1, 2026) · [announcement](https://www.linkedin.com/posts/rohithsurya_aiengineer-edgeai-multimodalai-ugcPost-7444515191498772480-uZbo) · [demo video](https://youtu.be/oJxBTXyXbbU)

## The idea: the LLM proposes, deterministic code decides

A 3B model on an edge device is slow and sometimes wrong, so it never touches a device directly.

```
voice / text / sensors
  → Classifier     exact phrases → deterministic fast path (no LLM)
                   anything else → LLM picks one of a fixed set of intents (JSON schema)
  → Orchestrator   policy gates (presence, guest mode), cooldowns, idempotency keys
  → Runner         timeouts, 1 retry on transient errors, circuit breaker, 12 s deadline
  → Home Assistant → read the device back to verify → SQLite memory + JSONL audit log
```

- **Worst case for a hallucination:** the model can pick an allowed, policy-gated intent. It can't invent a tool call, skip a policy or override a sensor. Presence comes from the mmWave sensor, never from the request.
- **Shadow mode:** `AGENT_MODE=shadow` plans and logs everything without touching devices, so behaviour changes can be compared before they go live (`evals/ab_report.py`).
- **Verify, don't trust:** every action is read back from the device. Across 718 real plans (Feb–Aug 2026), Home Assistant returned OK for 99% of calls, yet the read-back flagged 23% of plans as not matching the target state. Some were real device misses and some were verification timing. After settle-and-recheck reads went in (Mar 8), plan success rose from 66% to 84%.

## By the numbers

| | |
|---|---|
| Model | Ministral 3B Instruct (2512), 4-bit GGUF (2.15 GB) + vision projector (0.84 GB): about 3 GB on an 8 GB board |
| Serving | llama.cpp with CUDA, flash attention, 2048-token context, all layers on the GPU (`-ngl 99`) |
| Deterministic planning | ~0.1 ms per request, against seconds for an LLM call |
| Tests | 114 unit tests + 15 scenario evals (cooldowns, guest mode, presence, retries, hard failures, idempotency, shadow vs active), all in CI |

## What it does

- **Voice:** Home Assistant Assist (faster-whisper speech-to-text, Piper text-to-speech) and Siri via an Apple Home scene → `/agent/chat` → spoken reply on a HomePod.
- **Sensors:** Zigbee door and mmWave presence sensors over MQTT drive "entering the room" and vacancy automations.
- **Camera:** "is my desk messy?" runs a local vision analysis and caches the result.
- **Memory:** beliefs, preferences and episodes in SQLite. Feedback like "too cold" or "warmer next time" updates sleep preferences.
- **Intents:** `fan_on`, `fan_off`, `enter_room`, `sleep_mode`, `focus_start`, `focus_end`, `comfort_adjust`, `status`, `analyze_bedroom`, `no_action`.

## Timeline

- **Feb 2026:** started as a sensor-driven room agent.
- **Feb 28 – Mar 1:** hackathon build, with Ministral 3B served through Ollama. Won 1st Prize and the NVIDIA On-Device Track.
- **Mar 2026:** hardening. Moved to llama.cpp with CUDA, added the reliability layer (retry, circuit breaker, deadline), verification fixes and the eval harness.
- **Apr 2026:** a single classifier replaced the separate routers, and session memory and episode reflection were added.

## Design trade-offs

- **llama.cpp over TensorRT:** the vision and language parts would have to be split into separate TensorRT pipelines. On 8 GB that added too much complexity for the gain.
- **No KV-cache quantization:** a q4_0 KV cache made tool calls less reliable and increased hallucinations, so it was dropped.
- **Small model on purpose:** larger models (for example Qwen3.5-4B) spilled into swap on the 8 GB board.

## Hardware

- **Compute:** NVIDIA Jetson Orin Nano Super (8 GB)
- **Zigbee:** Home Assistant Connect ZBT-2 coordinator, mmWave presence sensor, door sensors, smart plug, temperature/humidity sensor
- **Climate:** Broadlink RM4 Mini IR blaster → window AC (SmartIR climate entity)
- **Lights:** smart plug for the bedside lamp, SwitchBot for the ceiling light
- **Audio:** HomePod (text-to-speech output)
- **Camera:** USB camera for room analysis

## Try it locally (no Home Assistant, broker or GPU needed)

```bash
cd apps/bedroom-agent
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

TOOL_BACKEND=local MQTT_ENABLED=false VISION_ANALYSIS_ENABLED=false \
  uvicorn src.app:app --port 9000

curl -s -X POST localhost:9000/agent/chat -H 'Content-Type: application/json' \
  -d '{"text": "turn the fan on"}'
```

With no presence sensor connected, the policy gate denies the request and explains why ("Fan blocked: I don't detect anyone in the room."). That's the safety layer working. The scenario evals simulate sensor states:

```bash
ruff check src tests ../../evals && pytest tests     # from apps/bedroom-agent
python ../../evals/run_all.py                        # 15/15 scenarios
```

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
    Router["Classifier"]
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
    Model["LLM provider\nllama.cpp (OpenAI-compatible API)"]
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
        Router["Classifier"]
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
    Services["Classifier / StatusService /\nDecisionEngine / Orchestrator"]
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

## Running against a mock Home Assistant

From the repo root, start the mock:

```bash
python -m uvicorn mock_ha.app:app --host 0.0.0.0 --port 8124 --reload
```

Then point the agent at it:

```bash
cd apps/bedroom-agent
TOOL_BACKEND=http HA_BASE_URL=http://127.0.0.1:8124 \
MQTT_ENABLED=false VISION_ANALYSIS_ENABLED=false \
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

The authoritative settings live in [apps/bedroom-agent/src/core/config.py](apps/bedroom-agent/src/core/config.py).

Common variables:

- `AGENT_MODE=shadow|active`
- `TOOL_BACKEND=local|http|ha`
- `HA_BASE_URL`, `HA_TOKEN`
- `LLM_BASE_URL`, `LLM_MODEL`, `OPENAI_API_KEY`, `LLM_CHAT_TEMPLATE_KWARGS` (JSON, e.g. `{"reasoning_strength": "low"}` for reasoning models)
- `LLM_DECISION_ENABLED`, `LLM_DECISION_TIMEOUT_S`, `LLM_DECISION_MIN_CONFIDENCE`
- `CLASSIFIER_FAST_PATH` (deterministic phrase routing before the LLM), `TOOL_RETRY_MAX_ATTEMPTS`
- `MQTT_ENABLED`, `MQTT_HOST`, `MQTT_PORT`, `Z2M_DOOR_TOPIC`, `Z2M_PRESENCE_TOPIC`
  `Z2M_DOOR_TOPIC` can be a comma-separated list when multiple door sensors should share the same entry logic.
- `SQLITE_PATH`
- `CAMERA_MODE=device|ha_snapshot|file`
- `VISION_ANALYSIS_ENABLED`, `VISION_FALLBACK_IMAGE_PATH`

## Verification

From `apps/bedroom-agent` (the same checks run in CI on every push and pull request):

```bash
ruff check src tests ../../evals
pytest tests
python ../../evals/run_all.py
```

## More Detail

Service-specific docs are in [apps/bedroom-agent/README.md](apps/bedroom-agent/README.md).
