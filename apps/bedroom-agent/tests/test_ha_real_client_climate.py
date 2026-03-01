from __future__ import annotations

from core.logging_jsonl import JsonlLogger
from tools.ha_real_client import HAToolClientReal
from contracts.ha import ToolCall


class DummyResponse:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code
        self.text = "{}"

    def json(self):
        return {"ok": True}


def test_ha_real_client_maps_climate_tools(monkeypatch, tmp_path):
    calls: list[tuple[str, dict]] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, json))
        return DummyResponse()

    monkeypatch.setattr("tools.ha_real_client.requests.post", fake_post)

    client = HAToolClientReal(
        base_url="http://ha.local",
        token="token",
        logger=JsonlLogger(log_dir=str(tmp_path), tz_name="America/New_York"),
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
    assert calls[0][0].endswith("/api/services/climate/set_hvac_mode")
    assert calls[1][0].endswith("/api/services/climate/set_temperature")
    assert calls[2][0].endswith("/api/services/climate/set_fan_mode")
