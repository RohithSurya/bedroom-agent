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
