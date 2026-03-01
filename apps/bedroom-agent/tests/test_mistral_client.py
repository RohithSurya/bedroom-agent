from __future__ import annotations

from llm.factory import build_llm_client
from llm.mistral_client import MistralClient
from llm.ollama_client import OllamaClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_mistral_client_sends_chat_completions_request(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"visible": true, "confidence": 0.9}'},
                    }
                ]
            }
        )

    monkeypatch.setattr("llm.mistral_client.requests.post", fake_post)

    client = MistralClient(
        api_key="secret",
        base_url="https://api.mistral.ai/v1",
        model="ministral-3b-2512",
        timeout_s=12.0,
    )
    out = client.generate_json(
        prompt="Is there a mirror in this room?",
        schema={"type": "object"},
        images_b64=["abc123"],
        temperature=0.0,
        num_predict=64,
    )

    assert out["visible"] is True
    assert captured["url"] == "https://api.mistral.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["timeout"] == 12.0
    assert captured["json"]["model"] == "ministral-3b-2512"
    assert captured["json"]["max_tokens"] == 64
    assert captured["json"]["response_format"]["type"] == "json_schema"
    content = captured["json"]["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"].startswith("data:image/jpeg;base64,abc123")


def test_llm_factory_selects_requested_provider():
    client = build_llm_client(
        provider="mistral",
        model="ministral-3b-2512",
        timeout_s=10.0,
        base_url="http://localhost:11434",
        mistral_api_key="secret",
    )
    assert isinstance(client, MistralClient)

    fallback = build_llm_client(
        provider="ollama",
        model="ministral-3:3b",
        timeout_s=10.0,
        base_url="http://localhost:11434",
    )
    assert isinstance(fallback, OllamaClient)
