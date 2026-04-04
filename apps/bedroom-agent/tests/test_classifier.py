from __future__ import annotations

from agent.classifier import Classifier
from memory.sqlite_kv import SqliteKV


def test_classifier_maps_explicit_mode_prompts():
    c = Classifier(llm=None)

    r = c.classify(text="Make the room ready for sleep", state={})
    assert r.intent == "sleep_mode"
    assert r.source == "deterministic"
    assert r.confidence == 0.99

    r = c.classify(text="Start sleep mode", state={})
    assert r.intent == "sleep_mode"

    r = c.classify(text="Set the room up for focus", state={})
    assert r.intent == "focus_start"

    r = c.classify(text="Make the room comfortable", state={})
    assert r.intent == "comfort_adjust"


def test_classifier_maps_status_and_analysis():
    c = Classifier(llm=None)

    r = c.classify(text="Why did the bedroom light turn on?", state={})
    assert r.intent == "status"
    assert r.args["query"] == "Why did the bedroom light turn on?"

    r = c.classify(text="Analyze my room", state={})
    assert r.intent == "analyze_bedroom"

    r = c.classify(text="Is this room good for focus?", state={})
    assert r.intent == "analyze_bedroom"

    r = c.classify(text="Do you see a monitor on the desk?", state={})
    assert r.intent == "analyze_bedroom"


def test_classifier_maps_focus_end():
    c = Classifier(llm=None)

    r = c.classify(text="End focus mode", state={})
    assert r.intent == "focus_end"

    r = c.classify(text="Turn off focus mode", state={})
    assert r.intent == "focus_end"


def test_classifier_maps_fan_control():
    c = Classifier(llm=None)

    r = c.classify(text="Turn the fan on", state={})
    assert r.intent == "fan_on"

    r = c.classify(text="Stop the fan", state={})
    assert r.intent == "fan_off"


def test_classifier_fallback_to_status():
    c = Classifier(llm=None)

    r = c.classify(text="hello there", state={})
    assert r.intent == "status"
    assert r.source == "fallback"
    assert r.confidence < 0.5


def test_classifier_detects_feedback_with_last_episode(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    kv.set("episodes", "last", {"intent": "sleep_mode", "ts": 1.0})

    c = Classifier(llm=None, kv=kv)

    r = c.classify(text="It was too cold last night", state={})
    assert r.intent == "preference_feedback"
    assert r.is_feedback is True
    assert r.args["last_intent"] == "sleep_mode"


def test_classifier_no_feedback_without_last_episode():
    c = Classifier(llm=None, kv=None)

    r = c.classify(text="It was too cold", state={})
    # Without kv, feedback detection is skipped — falls through to normal routing
    assert r.is_feedback is False


def test_classifier_no_feedback_without_episode(tmp_path):
    kv = SqliteKV(str(tmp_path / "test.db"))
    # No episode stored

    c = Classifier(llm=None, kv=kv)

    r = c.classify(text="It was too cold", state={})
    assert r.is_feedback is False


def test_classifier_ambiguous_falls_to_status_without_llm():
    """What was previously 'decision_request' now falls to status without LLM."""
    c = Classifier(llm=None)

    r = c.classify(text="What should happen now?", state={})
    # No longer a special intent — just falls through to status
    assert r.intent == "status"
