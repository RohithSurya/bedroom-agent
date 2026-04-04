"""Integration tests for the chat pipeline."""

from __future__ import annotations

import os

import pytest

# Ensure local backend + no vision + no LLM before importing app
os.environ.setdefault("TOOL_BACKEND", "local")
os.environ.setdefault("VISION_ANALYSIS_ENABLED", "false")
os.environ.setdefault("LLM_BASE_URL", "")
os.environ.setdefault("LLM_DECISION_ENABLED", "false")

from agent.pipeline import PipelineResult, run_chat_pipeline
from reliability.deadline import Deadline


@pytest.fixture()
def agent(tmp_path):
    """Build an AgentAppState with local backend, no LLM, using a temp SQLite DB."""
    os.environ["SQLITE_PATH"] = str(tmp_path / "pipeline_test.db")
    os.environ["TOOL_BACKEND"] = "local"
    os.environ["VISION_ANALYSIS_ENABLED"] = "false"
    os.environ["LLM_BASE_URL"] = ""
    os.environ["LLM_DECISION_ENABLED"] = "false"

    from core.config import Settings
    from app import AgentAppState

    settings = Settings()
    return AgentAppState(settings)


def _run(agent, text: str, session_id: str | None = None) -> PipelineResult:
    return run_chat_pipeline(
        text=text,
        state_overrides={},
        agent=agent,
        session_id=session_id,
        deadline=Deadline.from_now(5.0),
    )


def test_sleep_mode_returns_action(agent):
    result = _run(agent, "start sleep mode")
    assert result.mode == "action"
    assert result.input_info["intent"] == "sleep_mode"
    assert result.session_id is not None


def test_fan_on_returns_action(agent):
    result = _run(agent, "turn on the fan")
    assert result.mode == "action"
    assert result.input_info["intent"] == "fan_on"


def test_status_returns_info(agent):
    result = _run(agent, "what's the temperature?")
    assert result.mode == "info"


def test_focus_start_returns_action(agent):
    result = _run(agent, "start focus mode")
    assert result.mode == "action"
    assert result.input_info["intent"] == "focus_start"


def test_session_id_preserved(agent):
    result = _run(agent, "turn off the fan", session_id="test-session-1")
    assert result.session_id == "test-session-1"


def test_session_id_generated_when_none(agent):
    result = _run(agent, "fan on")
    assert result.session_id is not None
    assert len(result.session_id) > 0


def test_pipeline_result_has_timings(agent):
    result = _run(agent, "sleep mode")
    assert "classify" in result.timings_ms


def test_action_result_has_execution(agent):
    result = _run(agent, "fan on")
    assert result.execution is not None
    assert "success" in result.execution
