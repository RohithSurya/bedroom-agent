from __future__ import annotations

from types import SimpleNamespace

from llm.factory import build_llm_client
from llm.openai_client import OpenAIClient


def test_openai_client_sends_chat_completions_request(monkeypatch):
    captured = {"init": None, "calls": []}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["calls"].append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content='{"visible": true, "confidence": 0.9}'),
                    )
                ],
                usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 12}),
            )

    class FakeOpenAI:
        def __init__(self, *, base_url, api_key, timeout):
            captured["init"] = {"base_url": base_url, "api_key": api_key, "timeout": timeout}
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("llm.openai_client.OpenAI", FakeOpenAI)

    client = OpenAIClient(
        base_url="http://127.0.0.1:8081/v1",
        model="Ministral-3-3B-Instruct-2512-Q4_K_M.gguf",
        timeout_s=11.0,
    )
    out = client.generate_json(
        prompt="Is there a mirror in this room?",
        schema={"type": "object"},
        images_b64=["abc123"],
        temperature=0.0,
        num_predict=64,
    )

    assert out["visible"] is True
    assert captured["init"]["base_url"] == "http://127.0.0.1:8081/v1"
    assert captured["init"]["api_key"] == "not-needed"
    assert captured["init"]["timeout"] == 11.0
    assert len(captured["calls"]) == 1
    call = captured["calls"][0]
    assert call["model"] == "Ministral-3-3B-Instruct-2512-Q4_K_M.gguf"
    assert call["max_tokens"] == 64
    assert call["response_format"]["type"] == "json_schema"
    content = call["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,abc123")


def test_factory_builds_openai_client():
    client = build_llm_client(
        model="Ministral-3-3B-Instruct-2512-Q4_K_M.gguf",
        timeout_s=10.0,
        base_url="http://127.0.0.1:8081/v1",
        openai_api_key="",
    )
    assert isinstance(client, OpenAIClient)
