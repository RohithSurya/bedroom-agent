from __future__ import annotations

from agent.status_service import StatusService
from memory.sqlite_kv import SqliteKV


def test_status_service_explains_recent_light_on(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    kv.set("belief", "presence", True)
    kv.set("belief", "door_open", False)
    kv.append_event("door_update", {"door_open": True})
    kv.append_event("presence_update", {"presence": True, "topic": "presence"})
    kv.append_event("enter_detected", {"quiet_hours": False})

    service = StatusService(kv=kv, llm=None, tz_name="America/New_York")
    out = service.handle_query("Why did the light turn on?")

    assert "door opened" in out["summary"].lower()
    assert "presence" in out["summary"].lower()
    assert out["structured"]["query_type"] == "why_light_on"
    assert [event["type"] for event in out["structured"]["recent_events"]] == [
        "enter_detected",
        "door_update",
        "presence_update",
    ]


def test_status_service_summarizes_room_status(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    kv.set("belief", "presence", False)
    kv.set("belief", "door_open", False)
    kv.set("prefs", "guest_mode", False)
    kv.append_event("vacancy_detected", {"delay_s": 120})

    service = StatusService(kv=kv, llm=None, tz_name="America/New_York")
    out = service.handle_query("What is the room status?")

    assert "presence is not present" in out["summary"].lower()
    assert out["structured"]["beliefs"]["presence"] is False


def test_status_service_includes_live_light_status_in_room_query(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    kv.set("belief", "presence", True)
    kv.set("belief", "door_open", False)
    kv.set("prefs", "guest_mode", False)

    service = StatusService(kv=kv, llm=None, tz_name="America/New_York")
    out = service.handle_query(
        "Is the bedroom light on?",
        runtime_state={
            "light_entity_id": "light.bedroom_light",
            "light_state": "on",
            "bedroom_lamp_entity_id": "light.bedlamp",
            "bedroom_lamp_state": "off",
            "fan_entity_id": "fan.bedroom_fan",
            "fan_state": "off",
            "ac_entity_id": "climate.bedroom_ac",
            "ac_available": True,
            "ac_hvac_mode": "cool",
        },
    )

    assert "bedroom light is on" in out["summary"].lower()
    assert out["structured"]["live_status"]["light_state"] == "on"


def test_status_service_filters_presence_spam_for_why_light_on(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    kv.append_event("presence_update", {"presence": True, "topic": "presence", "target_distance": 1.2})
    kv.append_event("presence_update", {"presence": True, "topic": "presence", "target_distance": 1.1})
    kv.append_event("presence_update", {"presence": True, "topic": "presence", "target_distance": 1.0})

    service = StatusService(kv=kv, llm=None, tz_name="America/New_York")
    out = service.handle_query("Why did the light turn on?")

    assert "do not see a recent successful entry trigger" in out["summary"].lower()
    assert len(out["structured"]["recent_events"]) == 1
    assert out["structured"]["recent_events"][0]["type"] == "presence_update"


def test_status_service_persists_why_last_action_queries(tmp_path):
    kv = SqliteKV(str(tmp_path / "memory.sqlite"))
    kv.set(
        "decision",
        "last_trace",
        {
            "selected_intent": "sleep_mode",
            "selected_because": "the user asked to wind down",
            "signals": ["presence=True"],
            "guardrails": ["guest_mode_off"],
        },
    )

    service = StatusService(kv=kv, llm=None, tz_name="America/New_York")
    out = service.handle_query("Why was that the last action?")

    assert "sleep_mode" in out["summary"]
    assert kv.get("status", "last_summary") == out
    assert kv.recent_events(limit=1)[0]["type"] == "status_query_answered"
