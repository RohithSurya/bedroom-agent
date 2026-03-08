# Diagrams

These Mermaid sources describe the current implementation, not the earlier concept drafts.

## System Architecture

- `architecture.mmd`: full system view across Home Assistant, the FastAPI agent, storage, MQTT, camera input, and LLM providers
- `agent_runtime_class.mmd`: internal object model for `AgentAppState`, `Orchestrator`, `ActionFactory`, `Runner`, and `ToolBehaviorRegistry`
- `physical_integration.mmd`: room hardware, sensors, switches, gateways, and control paths
- `container_services.mmd`: Docker containers, host services, and runtime interactions across the deployed stacks

## Request and Action Flows

- `voice_chat_flow.mmd`: end-to-end Assist to `/agent/chat` flow
- `mqtt_entry_flow.mmd`: door and presence sensor flow into automatic entry and vacancy actions
- `analyze_bedroom.mmd`: camera capture and room-analysis path

## Data and State

- `data_architecture.mmd`: how beliefs, preferences, vision state, event memory, and JSONL logs are produced and consumed

## Notes

- `container_services.mmd` is the best diagram for Docker-level deployment topology.
- `physical_integration.mmd` is the best diagram for the real-world device and switch wiring.
- `voice_chat_flow.mmd` is the best diagram to start with if you want the top-level request lifecycle.
- `architecture.mmd` is the best whole-system diagram.
- `agent_runtime_class.mmd` is the best diagram if you want the exact orchestrator and runner internals.
- The older habit-engine concept diagram was removed because that subsystem is not implemented in the current code.
