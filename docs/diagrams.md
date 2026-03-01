# Diagrams

These Mermaid sources describe the current implementation, not the earlier concept drafts.

## System Architecture

- `architecture.mmd`: full system view across Home Assistant, the FastAPI agent, storage, MQTT, camera input, and LLM providers

## Request and Action Flows

- `voice_chat_flow.mmd`: end-to-end Assist to `/agent/chat` flow
- `night_mode.mmd`: direct `night_mode` execution through `/agent/run`
- `mqtt_entry_flow.mmd`: door and presence sensor flow into automatic entry and vacancy actions
- `analyze_bedroom.mmd`: camera capture and room-analysis path

## Data and State

- `data_architecture.mmd`: how beliefs, preferences, vision state, event memory, and JSONL logs are produced and consumed

## Notes

- `voice_chat_flow.mmd` is the best diagram to start with if you want the top-level request lifecycle.
- `architecture.mmd` is the best whole-system diagram.
- The older habit-engine concept diagram was removed because that subsystem is not implemented in the current code.
