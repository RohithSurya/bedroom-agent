# This is the correct Youtube URL: https://youtu.be/oJxBTXyXbbU.

The one published in the hackeriterate is the wrong link. Please consider my mistake and use this link to watch the demo



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

- **Camera** -> to feed photos to the local Ministral model backend
- **mmWave presence sensor** (Zigbee) → presence/occupancy belief state
- **Zigbee smart plug** → bedside lamp + power telemetry use-cases
- **Home Assistant Connect ZBT-2** (Zigbee coordinator, run in **Zigbee mode** for v1.0)
- **Broadlink RM4 Mini (IR blaster)** → controls **Vissani window AC**
- **Home Assistant + SmartIR climate entity** → reliable HVAC abstraction
- **HomePod Gen2** → TTS output + temp/humidity sensor (as available in Home ecosystem)

**Voice control path (locked Option 1)**

- **HomePod Siri → Apple Home Scene → Home Assistant → LLM/Agent → HomePod speaks**

### Sample Camera Photo

![Sample bedroom camera photo](apps/bedroom-agent/data/bedroom-latest.jpg)

This is a representative frame from the bedroom camera used by the vision path. A typical snapshot includes the bed, desk and monitor, chair, closet area, dresser, and mirror, and may also include a person in the room. The agent uses frames like this for `analyze bedroom` requests and for any vision-assisted room-state reasoning.

### Physical Components and Integration Diagram

```mermaid
flowchart LR
  User["User"]

  subgraph Room["Bedroom physical components"]
    HomePod["HomePod Gen2\nvoice in + TTS out"]
    Camera["USB camera\nroom snapshots"]
    Presence["mmWave presence sensor\nZigbee"]
    Door["Bedroom door sensor\nZigbee"]
    TempHum["Temp / humidity sensor\nHA entity"]
    Fan["Bedroom fan\nfan.bedroom_fan"]
    LightSwitch["Bedroom light\nlight.bedroom_light"]
    AC["Vissani window AC"]
  end

  subgraph Control["Bridges and control plane"]
    ZBT2["Home Assistant Connect ZBT-2\nZigbee coordinator"]
    Z2M["Zigbee2MQTT + MQTT broker"]
    HA["Home Assistant"]
    SmartIR["SmartIR climate entity\nclimate.bedroom_ac"]
    Broadlink["Broadlink RM4 Mini\nIR blaster"]
    Jetson["Jetson Orin Nano\nbedroom-agent + model runtime"]
  end

  User --> HomePod
  HomePod -->|voice request| HA
  HA -->|TTS playback| HomePod

  Camera -->|image capture| Jetson

  Presence -->|Zigbee telemetry| ZBT2
  Door -->|Zigbee telemetry| ZBT2
  TempHum -->|sensor state| HA
  Fan <-->|Zigbee control via fan entity| ZBT2
  LightSwitch <-->|Zigbee switch control| ZBT2

  ZBT2 --> Z2M
  Z2M -->|MQTT events| Jetson
  Z2M -->|entity updates| HA

  Jetson -->|REST tool requests| HA
  HA -->|entity state API| Jetson

  HA --> SmartIR
  SmartIR --> Broadlink
  Broadlink -->|IR commands| AC

  HA -->|switch service calls| Z2M
  Z2M --> ZBT2
```

### Container and Service Interaction Diagram

```mermaid
flowchart LR
  subgraph UserLayer["User and client layer"]
    User["User / HomePod Siri"]
  end

  subgraph HAStack["infra/home-automation/docker-compose.yaml"]
    HA["homeassistant\nhost network\n:8123"]
    MQTT["mosquitto\nhost network\n:1883"]
    Z2M["zigbee2mqtt\nhost network"]
  end

  subgraph AgentStack["apps/bedroom-agent/docker-compose.yml"]
    Agent["bedroom-agent\nFastAPI\n:9000"]
  end

  subgraph VoiceStack["wyoming/docker-compose.yaml"]
    Whisper["faster-whisper\nWyoming STT"]
  end

  subgraph External["External or host-level services"]
    LLM["LLM backend\nOllama :11434 or Mistral API"]
    Broadlink["Broadlink RM4 Mini"]
    HomePod["HomePod Gen2"]
    Zigbee["Zigbee devices\nsensor + switch mesh"]
    Camera["USB camera / video device"]
  end

  User --> HomePod
  HomePod -->|voice request| HA
  HA -->|TTS / media playback| HomePod

  HA -->|Assist pipeline / STT| Whisper

  HA -->|REST commands\n/agent/run /agent/chat| Agent
  Agent -->|HA state + service API| HA

  Agent -->|MQTT subscribe\npresence + door topics| MQTT
  Z2M -->|publish telemetry| MQTT
  MQTT -->|MQTT discovery + entity updates| HA

  Z2M -->|USB serial / Zigbee radio| Zigbee
  HA -->|device control via MQTT entities| MQTT

  Agent -->|generate / structured output| LLM
  Agent -->|capture snapshots| Camera

  HA -->|SmartIR climate control| Broadlink
```


## Architecture

At runtime the agent looks like this:

1. Home Assistant or a caller sends `POST /agent/run` or `POST /agent/chat`.
2. The app builds a runtime state from Home Assistant entities plus stored beliefs and preferences.
3. `NLRouter` maps text to a high-level intent.
4. `Orchestrator` uses `ActionFactory` to compose typed actions and materializes them into `ToolCall` objects.
5. `Runner` resolves each `ToolCall` through `ToolBehaviorRegistry`, then executes, verifies, cools down, and logs results.
6. MQTT listeners update occupancy and door beliefs continuously in the background.
7. Optional vision analysis captures a bedroom image and asks the configured LLM/VLM for structured output.

### System Architecture Diagram

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
```

### Internal Runtime Class Diagram

```mermaid
classDiagram
    class AgentAppState {
        +build_runtime_state(extra_state, intent) dict
        +orchestrator: Orchestrator
        +runner: Runner
    }

    class Orchestrator {
        +handle_request(intent, args, state) dict
        +action_factory: ActionFactory
        -_resolve_light_entity_id(args, state) str
        -_materialize_actions(correlation_id, actions) list~ToolCall~
    }

    class ActionFactory {
        +light(entity_id, state) LightAction
        +fan(entity_id, state) FanAction
        +speech(message) SpeechAction
        +climate(entity_id, hvac_mode, temperature, fan_mode) ClimatePlan
    }

    class AgentAction {
        <<interface>>
        +to_tool_calls(correlation_id) list~ToolCall~
    }

    class LightAction {
        +entity_id: str
        +state: str
        +to_tool_calls(correlation_id) list~ToolCall~
    }

    class FanAction {
        +entity_id: str
        +state: str
        +to_tool_calls(correlation_id) list~ToolCall~
    }

    class SpeechAction {
        +message: str
        +to_tool_calls(correlation_id) list~ToolCall~
    }

    class ClimatePlan {
        +entity_id: str
        +hvac_mode: str
        +temperature: int?
        +fan_mode: str?
        +to_tool_calls(correlation_id) list~ToolCall~
    }

    class ToolCall {
        +tool: str
        +args: dict
        +idempotency_key: str
        +correlation_id: str
        +timeout_s: float?
    }

    class Runner {
        +execute_actions(correlation_id, actions, cooldown_key, cooldown_seconds, deadline) dict
        +read_entity_state(entity_id) dict
        +behavior_registry: ToolBehaviorRegistry
    }

    class ToolBehaviorRegistry {
        +for_call(call) ToolBehavior
    }

    class ToolBehavior {
        <<interface>>
        +is_retryable(call) bool
        +verify(runner, call, result) dict
        +is_verification_critical(call) bool
    }

    class BaseToolBehavior
    class LightSetBehavior
    class FanSetBehavior
    class SwitchSetBehavior
    class ClimateSetModeBehavior
    class ClimateSetTemperatureBehavior
    class ClimateSetFanModeBehavior
    class TtsSayBehavior
    class DefaultToolBehavior

    class ToolExecutor {
        +execute(call) ToolResult
        +get_state() dict
    }

    class HAToolClientHTTP {
        +execute(call) ToolResult
    }

    class HAToolClientReal {
        +execute(call) ToolResult
        +read_entity_state(entity_id) dict
    }

    class ToolResult {
        +ok: bool
        +tool: str
        +details: dict
    }

    AgentAppState --> Orchestrator
    AgentAppState --> Runner

    Orchestrator --> ActionFactory
    ActionFactory --> AgentAction
    LightAction ..|> AgentAction
    FanAction ..|> AgentAction
    SpeechAction ..|> AgentAction
    ClimatePlan ..|> AgentAction

    AgentAction --> ToolCall

    Runner --> ToolBehaviorRegistry
    ToolBehaviorRegistry --> ToolBehavior
    BaseToolBehavior ..|> ToolBehavior
    LightSetBehavior --|> BaseToolBehavior
    FanSetBehavior --|> BaseToolBehavior
    SwitchSetBehavior --|> BaseToolBehavior
    ClimateSetModeBehavior --|> BaseToolBehavior
    ClimateSetTemperatureBehavior --|> BaseToolBehavior
    ClimateSetFanModeBehavior --|> BaseToolBehavior
    TtsSayBehavior --|> BaseToolBehavior
    DefaultToolBehavior --|> BaseToolBehavior

    Runner --> ToolCall
    Runner --> ToolResult
    Runner --> ToolExecutor
    Runner --> HAToolClientHTTP
    Runner --> HAToolClientReal
```

### Data Architecture Diagram

```mermaid
flowchart TD
  subgraph Inputs["Input streams"]
    Voice["Voice or HTTP request"]
    EntityReads["Live Home Assistant entity reads"]
    MqttEvents["MQTT door / presence payloads"]
    Images["Bedroom snapshots"]
  end

  subgraph Memory["SQLite memory.sqlite"]
    Belief["belief namespace\npresence door_open last_door_open_ts"]
    Prefs["prefs namespace\nguest_mode and user toggles"]
    VisionState["vision namespace\nlatest_bedroom_analysis"]
    StatusState["status namespace\nlast_summary"]
    Events["events table\nappend-only typed events"]
  end

  subgraph Runtime["Runtime context"]
    StatePacket["build_runtime_state()\nstate packet for routing and policy"]
    Services["Router / StatusService /\nDecisionEngine / Orchestrator"]
  end

  Logs["logs/events.jsonl\ncorrelation-based execution log"]

  MqttEvents --> Belief
  MqttEvents --> Events
  Images --> VisionState
  Images --> Events

  Belief --> StatePacket
  Prefs --> StatePacket
  VisionState --> StatePacket
  EntityReads --> StatePacket
  Voice --> StatePacket

  StatePacket --> Services
  Services --> StatusState
  Services --> Events
  Services --> Logs
```

### Voice Chat Flow Diagram

```mermaid
sequenceDiagram
  actor User
  participant Assist as HA Assist
  participant Auto as HA conversation automation
  participant API as /agent/chat
  participant State as Runtime state builder
  participant Router as NLRouter
  participant Status as StatusService
  participant Vision as BedroomRoomAnalyzer
  participant Decide as DecisionEngine
  participant Orch as Orchestrator
  participant Factory as ActionFactory
  participant Run as Runner
  participant Registry as ToolBehaviorRegistry
  participant Behavior as ToolBehavior
  participant Tool as HA tool client
  participant HAApi as HA service API

  User->>Assist: "Start focus mode"
  Assist->>Auto: matched catch-all conversation trigger
  Auto->>API: POST /agent/chat
  API->>State: build_runtime_state()
  State-->>API: runtime state
  API->>Router: route(text, state)

  alt intent=status
    Router-->>API: status
    API->>Status: handle_query()
    Status-->>API: mode=info result
  else intent=analyze_bedroom
    Router-->>API: analyze_bedroom
    API->>Vision: analyze()
    Vision-->>API: mode=info result
  else intent=decision_request
    Router-->>API: decision_request
    API->>Decide: choose_intent()
    Decide-->>API: chosen_intent + rationale
    API->>Orch: handle_request(chosen_intent)
    Orch->>Factory: build AgentAction objects
    Factory-->>Orch: typed actions
    Orch-->>API: policy + actions
    API->>Run: execute_actions()
    loop each ToolCall
      Run->>Registry: for_call(call)
      Registry-->>Run: ToolBehavior
      Run->>Tool: execute(call)
      Tool->>HAApi: service/state API
      HAApi-->>Tool: response
      Tool-->>Run: ToolResult
      Run->>Behavior: verify(call, result)
    end
    Run-->>API: execution
  else routed action intent
    Router-->>API: focus_start / sleep_mode / fan_on ...
    API->>Orch: handle_request(intent)
    Orch->>Factory: build AgentAction objects
    Factory-->>Orch: typed actions
    Orch-->>API: policy + actions
    API->>Run: execute_actions()
    loop each ToolCall
      Run->>Registry: for_call(call)
      Registry-->>Run: ToolBehavior
      Run->>Tool: execute(call)
      Tool->>HAApi: service/state API
      HAApi-->>Tool: response
      Tool-->>Run: ToolResult
      Run->>Behavior: verify(call, result)
    end
    Run-->>API: execution
  end

  API-->>Auto: JSON response
  Auto->>Assist: set_conversation_response()
  Assist-->>User: spoken reply
```

### MQTT Entry Flow Diagram

```mermaid
sequenceDiagram
  participant Door as Door sensor
  participant Presence as mmWave sensor
  participant Broker as MQTT broker
  participant Listener as Z2MMqttListener
  participant DB as SQLite
  participant App as AgentAppState callback
  participant Orch as Orchestrator
  participant Factory as ActionFactory
  participant Run as Runner
  participant Registry as ToolBehaviorRegistry
  participant Tool as HA tool client
  participant HA as Home Assistant API

  Door->>Broker: contact=false
  Broker->>Listener: door topic payload
  Listener->>DB: set belief.door_open=true
  Listener->>DB: set belief.last_door_open_ts
  Listener->>DB: append door_update

  Presence->>Broker: presence=true
  Broker->>Listener: presence topic payload
  Listener->>DB: set belief.presence=true
  Listener->>DB: append presence_update

  alt within ENTRY_WINDOW_S and not on cooldown
    Listener->>DB: append enter_detected
    Listener->>App: on_enter callback
    App->>Orch: handle_request(enter_room)
    Orch->>Factory: build LightAction
    Factory-->>Orch: typed action
    Orch-->>App: light-on ToolCall plan
    App->>Run: execute_actions(plan)
    Run->>Registry: for_call(light.set)
    Registry-->>Run: LightSetBehavior
    Run->>Tool: execute(light.set)
    Tool->>HA: turn on light
  else presence becomes false
    Listener->>DB: start vacancy timer
    Note over Listener: after VACANCY_OFF_DELAY_S
    Listener->>DB: append vacancy_detected
    Listener->>App: on_vacant callback
    App->>Run: execute_actions([light.set off])
    Run->>Registry: for_call(light.set)
    Registry-->>Run: LightSetBehavior
    Run->>Tool: execute(light.set)
    Tool->>HA: turn off light
    Listener->>DB: append vacancy_off_executed
  end
```

Mermaid source files also live in `docs/`:

- `docs/architecture.mmd`
- `docs/agent_runtime_class.mmd`
- `docs/container_services.mmd`
- `docs/physical_integration.mmd`
- `docs/voice_chat_flow.mmd`
- `docs/mqtt_entry_flow.mmd`
- `docs/data_architecture.mmd`
- `docs/analyze_bedroom.mmd`
- `docs/diagrams.md`

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
- `LLM_BASE_URL` and `LLM_MODEL`: local model backend settings
- `OPENAI_API_KEY`: optional for local OpenAI-compatible servers
- `MQTT_HOST`, `MQTT_PORT`, `Z2M_DOOR_TOPIC`, `Z2M_PRESENCE_TOPIC`: Zigbee2MQTT integration
- `CAMERA_MODE`, `CAMERA_DEVICE`, `VISION_FALLBACK_IMAGE_PATH`: image capture configuration

For a full list, see `apps/bedroom-agent/src/core/config.py`.

## Home Assistant Integration

Home Assistant configuration lives under `infra/home-automation/ha_config`.

- `configuration.yaml` defines `rest_command` calls into the agent
- `automations.yaml` routes every Assist utterance to `/agent/chat`
- `scripts.yaml` exposes helper scripts such as `agent_chat_request` and `bedroom_agent_speak`

The current voice entrypoint is a catch-all Assist trigger, so prompts like these all go through the agent LLM:

- `start focus mode`
- `cool the room`
- `analyze bedroom`
- `check my bedroom`

This will also shadow normal built-in Assist intent handling unless you narrow the trigger again.

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
