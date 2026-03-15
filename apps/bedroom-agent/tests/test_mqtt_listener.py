from __future__ import annotations

import json

from agent.mqtt_listener import Z2MMqttListener
from memory.sqlite_kv import SqliteKV


class FakeMQTTMessage:
    def __init__(self, topic: str, payload: dict[str, object]) -> None:
        self.topic = topic
        self.payload = json.dumps(payload).encode("utf-8")


class FakeMQTTClient:
    def __init__(self) -> None:
        self.subscriptions: list[str] = []

    def subscribe(self, topic: str) -> None:
        self.subscriptions.append(topic)


def _build_listener(tmp_path, door_topics: str | tuple[str, ...]):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    enter_calls: list[dict[str, object]] = []
    vacant_calls: list[dict[str, object]] = []
    listener = Z2MMqttListener(
        mqtt_host="localhost",
        mqtt_port=1883,
        mqtt_username=None,
        mqtt_password=None,
        door_topics=door_topics,
        presence_topic="zigbee2mqtt/bedroom_mmwave_sensor",
        tz_name="America/New_York",
        quiet_start="00:00",
        quiet_end="00:00",
        entry_window_s=15,
        entry_cooldown_s=10,
        vacancy_off_delay_s=120,
        kv=kv,
        on_enter=lambda meta: enter_calls.append(meta),
        on_vacant=lambda meta: vacant_calls.append(meta),
    )
    return listener, kv, enter_calls, vacant_calls


def test_bathroom_door_sensor_uses_same_entry_logic(tmp_path):
    listener, kv, enter_calls, vacant_calls = _build_listener(
        tmp_path,
        (
            "zigbee2mqtt/bedroom_door_sensor",
            "zigbee2mqtt/bathroom_door_sensor",
        ),
    )

    listener._on_message(
        None,
        None,
        FakeMQTTMessage("zigbee2mqtt/bathroom_door_sensor", {"contact": False}),
    )
    listener._on_message(
        None,
        None,
        FakeMQTTMessage("zigbee2mqtt/bedroom_mmwave_sensor", {"presence": True}),
    )

    assert kv.get("belief", "door_open") is True
    assert kv.get("belief", "presence") is True
    assert len(enter_calls) == 1
    assert vacant_calls == []

    door_events = kv.recent_events(limit=5, event_type="door_update")
    assert len(door_events) == 1
    assert door_events[0]["payload"]["topic"] == "zigbee2mqtt/bathroom_door_sensor"


def test_listener_subscribes_all_configured_door_topics(tmp_path):
    listener, kv, _, _ = _build_listener(
        tmp_path,
        "zigbee2mqtt/bedroom_door_sensor, zigbee2mqtt/bathroom_door_sensor",
    )
    client = FakeMQTTClient()

    listener._on_connect(client, None, {}, 0)

    assert client.subscriptions == [
        "zigbee2mqtt/bedroom_door_sensor",
        "zigbee2mqtt/bathroom_door_sensor",
        "zigbee2mqtt/bedroom_mmwave_sensor",
    ]

    event = kv.recent_events(limit=1, event_type="mqtt_connected")[0]
    assert event["payload"]["door_topic"] == "zigbee2mqtt/bedroom_door_sensor"
    assert event["payload"]["door_topics"] == [
        "zigbee2mqtt/bedroom_door_sensor",
        "zigbee2mqtt/bathroom_door_sensor",
    ]
