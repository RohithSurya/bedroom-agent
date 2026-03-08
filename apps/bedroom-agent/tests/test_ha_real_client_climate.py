from __future__ import annotations

from contracts.ha import ToolCall
from core.logging_jsonl import JsonlLogger
from tools.ha_real_client import HAToolClientReal


class DummyResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.text = "{}"

    def json(self):
        return {"ok": True}


class DummySession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, json))
        return DummyResponse()


def test_ha_real_client_maps_climate_tools(tmp_path):
    session = DummySession()

    client = HAToolClientReal(
        base_url="http://ha.local",
        token="token",
        logger=JsonlLogger(log_dir=str(tmp_path), tz_name="America/New_York"),
        session=session,
    )

    mode_result = client.execute(
        ToolCall(
            tool="climate.set_mode",
            args={"entity_id": "climate.bedroom_ac", "hvac_mode": "cool"},
            idempotency_key="1",
            correlation_id="cid",
        )
    )
    temp_result = client.execute(
        ToolCall(
            tool="climate.set_temperature",
            args={"entity_id": "climate.bedroom_ac", "temperature": 24},
            idempotency_key="2",
            correlation_id="cid",
        )
    )
    fan_result = client.execute(
        ToolCall(
            tool="climate.set_fan_mode",
            args={"entity_id": "climate.bedroom_ac", "fan_mode": "auto"},
            idempotency_key="3",
            correlation_id="cid",
        )
    )

    assert mode_result.ok is True
    assert temp_result.ok is True
    assert fan_result.ok is True
    assert session.calls[0][0].endswith("/api/services/climate/set_hvac_mode")
    assert session.calls[1][0].endswith("/api/services/climate/set_temperature")
    assert session.calls[2][0].endswith("/api/services/climate/set_fan_mode")


def test_ha_real_client_maps_fan_tool(tmp_path):
    session = DummySession()

    client = HAToolClientReal(
        base_url="http://ha.local",
        token="token",
        logger=JsonlLogger(log_dir=str(tmp_path), tz_name="America/New_York"),
        session=session,
    )

    on_result = client.execute(
        ToolCall(
            tool="fan.set",
            args={"entity_id": "fan.bedroom_fan", "state": "on"},
            idempotency_key="fan-on",
            correlation_id="cid",
        )
    )
    off_result = client.execute(
        ToolCall(
            tool="fan.set",
            args={"entity_id": "fan.bedroom_fan", "state": "off"},
            idempotency_key="fan-off",
            correlation_id="cid",
        )
    )

    assert on_result.ok is True
    assert off_result.ok is True
    assert session.calls[0][0].endswith("/api/services/fan/turn_on")
    assert session.calls[1][0].endswith("/api/services/fan/turn_off")
