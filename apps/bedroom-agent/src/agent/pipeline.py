"""Chat pipeline — testable function that encapsulates the full /agent/chat flow.

Extracts the classify → route → plan → execute → reflect pipeline so
that the HTTP handler is a thin wrapper.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from agent.classifier import Classification
from memory.session import Turn
from reliability.deadline import Deadline

if TYPE_CHECKING:
    from app import AgentAppState


@dataclass
class PipelineResult:
    mode: str  # "action" | "info" | "memory_update"
    input_info: dict[str, Any]
    classification: Classification | None = None
    decision: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)
    execution: dict[str, Any] | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None


def run_chat_pipeline(
    *,
    text: str,
    state_overrides: dict[str, Any],
    agent: AgentAppState,
    session_id: str | None,
    deadline: Deadline,
) -> PipelineResult:
    """Run the full chat pipeline: classify → route → plan → execute → reflect.

    Returns a PipelineResult with all information needed for the HTTP response.
    """
    t0 = time.perf_counter()

    # 1. Session (short-term memory)
    sid = agent.session_store.get_or_create(session_id)
    session_turns = agent.session_store.recent_turns(sid)

    # 2. Classify
    classification = agent.classifier.classify(text=text, state={}, session_turns=session_turns)
    print(f"Classified as {classification}")
    intent = classification.intent
    args = classification.args
    t_classify = time.perf_counter()

    # 3. Preference feedback short-circuit
    if classification.is_feedback:
        last_episode = agent.memory.get_last_episode()
        feedback_result = agent.preference_feedback.apply(
            user_text=text,
            last_episode=last_episode,
        )
        if feedback_result is not None:
            now = time.time()
            agent.session_store.add_turn(
                sid, Turn(role="user", text=text, intent="preference_feedback", ts=now)
            )
            agent.session_store.add_turn(
                sid,
                Turn(
                    role="agent",
                    text=feedback_result["message"],
                    intent="preference_feedback",
                    ts=now,
                ),
            )
            return PipelineResult(
                mode="memory_update",
                input_info={"text": text, "intent": "preference_feedback", "args": args},
                classification=classification,
                session_id=sid,
                extra={
                    "message": feedback_result["message"],
                    "updates": feedback_result["updates"],
                    "last_intent": feedback_result["intent_scope"],
                },
            )
        # Feedback didn't match — fall through to status
        intent = "status"
        args = {"query": text}

    # 4. Build runtime state
    t_before_state = time.perf_counter()
    state = agent.build_runtime_state(state_overrides, intent=intent, user_text=text)
    t_state = time.perf_counter()

    # 5. Info intents
    if intent == "status":
        result = agent.status_service.handle_query(
            args.get("query", text),
            runtime_state=state,
        )
        t_status = time.perf_counter()
        return PipelineResult(
            mode="info",
            input_info={"text": text, "intent": intent, "args": args},
            classification=classification,
            session_id=sid,
            extra={"result": result},
            timings_ms={
                "classify": round((t_classify - t0) * 1000, 1),
                "state": round((t_state - t_before_state) * 1000, 1),
                "status_service": round((t_status - t_state) * 1000, 1),
            },
        )

    if intent == "analyze_bedroom":
        result = agent.room_analyzer.analyze(text)
        t_analyze = time.perf_counter()
        return PipelineResult(
            mode="info",
            input_info={"text": text, "intent": intent, "args": args},
            classification=classification,
            session_id=sid,
            extra={"result": result},
            timings_ms={
                "classify": round((t_classify - t0) * 1000, 1),
                "analyze_bedroom": round((t_analyze - t_classify) * 1000, 1),
            },
        )

    # 6. Low-confidence ambiguity resolution via DecisionEngine
    if classification.confidence < 0.6 and agent.decision_engine.llm is not None:
        choice = agent.decision_engine.choose_intent(
            source="user_chat",
            trigger="chat_request",
            user_text=text,
            state=state,
        )
        intent = choice.intent
        args = choice.args
        agent.kv.set("decision", "last_trace", choice.trace)
        agent.kv.set(
            "decision",
            "last_choice",
            {
                "intent": choice.intent,
                "confidence": choice.confidence,
                "rationale": choice.rationale,
                "reasoning_tags": choice.reasoning_tags,
                "fallback_used": choice.fallback_used,
                "trace": choice.trace,
            },
        )
        if intent == "no_action":
            agent.record_episode(
                user_text=text,
                intent=intent,
                state=state,
                decision={"decision": "allow", "reason": "no_action"},
                actions=[],
                execution={"success": True, "failures": [], "executed_tools": []},
                memory_hits=list(state.get("relevant_prefs", {}).keys()),
            )
            return PipelineResult(
                mode="action",
                input_info={"text": text, "intent": intent, "args": args},
                classification=classification,
                session_id=sid,
                decision={
                    "chosen_intent": choice.intent,
                    "confidence": choice.confidence,
                    "rationale": choice.rationale,
                    "reasoning_tags": choice.reasoning_tags,
                    "fallback_used": choice.fallback_used,
                    "trace": choice.trace,
                },
                actions=[],
                execution={"success": True, "failures": [], "executed_tools": []},
                timings_ms={
                    "classify": round((t_classify - t0) * 1000, 1),
                    "state": round((t_state - t_before_state) * 1000, 1),
                },
                extra={"policy": {"decision": "allow", "reason": "no_action"}},
            )

    # 7. Record classification trace
    agent.kv.set(
        "decision",
        "last_choice",
        {
            "intent": intent,
            "confidence": classification.confidence,
            "rationale": f"Classifier ({classification.source}) matched to {intent}.",
            "reasoning_tags": [classification.source, intent],
            "fallback_used": classification.source == "fallback",
        },
    )
    agent.kv.set(
        "decision",
        "last_trace",
        {
            "goal": text,
            "selected_intent": intent,
            "selected_because": f"Classifier ({classification.source}) matched to {intent}.",
            "reasoning_tags": [classification.source, intent],
            "memory_hits": list(state.get("relevant_prefs", {}).keys())[:5],
            "episode_summary": str(state.get("episode_summary", "") or ""),
            "signals": [
                f"presence={bool(state.get('presence', False))}",
                f"temperature_c={state.get('temperature_c')}",
                f"humidity_pct={state.get('humidity_pct')}",
                f"ac_available={bool(state.get('ac_available', False))}",
            ],
            "guardrails": [
                "guest_mode_on" if bool(state.get("guest_mode", False)) else "guest_mode_off",
                "sleep_mode_enable_climate"
                if bool(state.get("sleep_mode_enable_climate", False))
                else "sleep_mode_climate_disabled",
            ],
            "fallback_used": classification.source == "fallback",
        },
    )

    # 8. Plan + execute
    plan = agent.orchestrator.handle_request(intent=intent, args=args, state=state)
    t_plan = time.perf_counter()
    execution = agent.runner.execute_actions(
        correlation_id=plan["correlation_id"],
        actions=plan["actions"],
        cooldown_key=plan.get("cooldown_key"),
        cooldown_seconds=int(plan.get("cooldown_seconds", 0)),
        deadline=deadline,
    )
    episode = agent.record_episode(
        user_text=text,
        intent=intent,
        state=state,
        decision=plan["decision"].model_dump(),
        actions=[a.model_dump() for a in plan["actions"]],
        execution=execution,
        memory_hits=list(state.get("relevant_prefs", {}).keys()),
    )

    # 9. Post-episode reflection (async, non-blocking)
    threading.Thread(
        target=agent.reflector.reflect,
        args=(episode, text),
        daemon=True,
    ).start()

    # 10. Record session turns
    now = time.time()
    agent.session_store.add_turn(sid, Turn(role="user", text=text, intent=intent, ts=now))
    agent.session_store.add_turn(
        sid, Turn(role="agent", text=f"Executed {intent}", intent=intent, ts=now)
    )

    t_exec = time.perf_counter()

    return PipelineResult(
        mode="action",
        input_info={"text": text, "intent": intent, "args": args},
        classification=classification,
        session_id=sid,
        decision=plan["decision"].model_dump(),
        actions=[a.model_dump() for a in plan["actions"]],
        execution=execution,
        timings_ms={
            "classify": round((t_classify - t0) * 1000, 1),
            "state": round((t_state - t_before_state) * 1000, 1),
            "plan": round((t_plan - t_state) * 1000, 1),
            "exec": round((t_exec - t_plan) * 1000, 1),
        },
        extra={"correlation_id": plan["correlation_id"]},
    )
