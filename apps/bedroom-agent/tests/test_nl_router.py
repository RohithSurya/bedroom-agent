from __future__ import annotations

from agent.nl_router import NLRouter


def test_router_maps_demo_prompts_to_status_and_analysis():
    router = NLRouter(llm=None)

    intent, args = router.route(text="Why did the bedroom light turn on?", state={})
    assert intent == "status"
    assert args["query"] == "Why did the bedroom light turn on?"

    intent, args = router.route(text="Analyze my room", state={})
    assert intent == "analyze_bedroom"

    intent, args = router.route(text="Is this room good for focus?", state={})
    assert intent == "analyze_bedroom"

    intent, args = router.route(text="Do you see a monitor on the desk?", state={})
    assert intent == "analyze_bedroom"


def test_router_maps_explicit_mode_prompts_directly():
    router = NLRouter(llm=None)

    intent, _ = router.route(text="Make the room ready for sleep", state={})
    assert intent == "sleep_mode"

    intent, _ = router.route(text="Start sleep mode", state={})
    assert intent == "sleep_mode"

    intent, _ = router.route(text="Set the room up for focus", state={})
    assert intent == "focus_start"

    intent, _ = router.route(text="Make the room comfortable", state={})
    assert intent == "comfort_adjust"


def test_router_keeps_ambiguous_high_level_requests_on_decision_path():
    router = NLRouter(llm=None)

    intent, _ = router.route(text="What should happen now?", state={})
    assert intent == "decision_request"


def test_router_maps_focus_end_directly():
    router = NLRouter(llm=None)

    intent, _ = router.route(text="End focus mode", state={})
    assert intent == "focus_end"

    intent, _ = router.route(text="Turn off focus mode", state={})
    assert intent == "focus_end"
