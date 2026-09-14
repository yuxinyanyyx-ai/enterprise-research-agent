"""Stable structural, fact, negative, and format assertions for Agent Eval."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent.context_budget import estimate_input_tokens
from tests.agent_eval.runner import CaseResult, TurnResult
from tests.agent_eval.schema import AgentCase, CallExpectation


def _context(case_id: str, turn: TurnResult) -> str:
    return (
        f"case={case_id} turn={turn.index + 1} "
        f"nodes={turn.executed_nodes} pending={turn.pending_nodes} "
        f"calls={turn.call_deltas}"
    )


def _state_value(state: dict[str, Any], dotted_path: str) -> Any:
    value: Any = state
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise AssertionError(f"state path not found: {dotted_path}")
        value = value[part]
    return value


def _assert_calls(actual: dict[str, int], expected: CallExpectation, context: str) -> None:
    for name in ("llm", "dmf", "document_extract", "export", "watchlist"):
        expected_value = getattr(expected, name)
        if expected_value is not None:
            assert actual[name] == expected_value, (
                f"{context}: expected {name} calls={expected_value}, got {actual[name]}"
            )


def assert_case_result(case: AgentCase, result: CaseResult) -> None:
    assert result.case_id == case.id
    assert len(result.turns) == len(case.turns)

    for turn_spec, turn in zip(case.turns, result.turns, strict=True):
        expected = turn_spec.expected
        context = _context(case.id, turn)
        if expected.route is not None:
            observed_nodes = [*turn.executed_nodes, *turn.pending_nodes]
            assert expected.route in observed_nodes, (
                f"{context}: expected route/node {expected.route!r}"
            )
        if expected.interrupt is not None:
            assert turn.interrupted is expected.interrupt, (
                f"{context}: expected interrupt={expected.interrupt}"
            )
        for path, expected_value in expected.state.items():
            actual = _state_value(turn.state, path)
            assert actual == expected_value, (
                f"{context}: state {path!r} expected {expected_value!r}, got {actual!r}"
            )

        answer = turn.answer
        for text in expected.answer.contains:
            assert text in answer, f"{context}: answer missing required text {text!r}"
        for text in expected.answer.not_contains:
            assert text not in answer, f"{context}: answer contains forbidden text {text!r}"
        for pattern in expected.answer.regex:
            assert re.search(pattern, answer), (
                f"{context}: answer does not match regex {pattern!r}"
            )
        for header in expected.answer.markdown_headers:
            assert f"| {header} " in answer, (
                f"{context}: answer missing Markdown header {header!r}"
            )
        if expected.answer.empty is not None:
            assert (not answer) is expected.answer.empty, (
                f"{context}: expected answer empty={expected.answer.empty}"
            )

        actual_artifacts = {
            artifact.get("tool_name")
            for artifact in turn.state.get("tool_artifacts", [])
        }
        for artifact_type in expected.artifact_types:
            assert artifact_type in actual_artifacts, (
                f"{context}: missing artifact {artifact_type!r}"
            )
        _assert_calls(turn.call_deltas, expected.call_deltas, context)
        if expected.llm_inputs and result.mode == "deterministic":
            count = turn.call_deltas["llm"]
            events = [event for event in turn.call_log if event["boundary"] == "llm"]
            events = events[-count:] if count else []
            for check in expected.llm_inputs:
                label = f"{context}: llm input {check.call_index}"
                assert check.call_index < len(events), f"{label}: missing call"
                event = events[check.call_index]
                messages = event["messages"]
                text = json.dumps([message.model_dump() for message in messages], ensure_ascii=False, default=str)
                if check.call_type is not None:
                    assert event["call_type"] == check.call_type, f"{label}: wrong call type"
                if check.max_messages is not None:
                    assert len(messages) <= check.max_messages, f"{label}: message limit exceeded"
                if check.max_chars is not None:
                    assert len(text) <= check.max_chars, f"{label}: character limit exceeded"
                if check.max_estimated_tokens is not None:
                    assert estimate_input_tokens(messages, event.get("tool_schemas", [])) <= check.max_estimated_tokens, f"{label}: token estimate limit exceeded"
                if check.message_types is not None:
                    assert [message.type for message in messages] == check.message_types, f"{label}: wrong message order"
                for marker in check.contains:
                    assert marker in text, f"{label}: missing required marker"
                for marker in check.not_contains:
                    assert marker not in text, f"{label}: forbidden marker present"
                if check.current_query_verbatim:
                    assert any(isinstance(message, HumanMessage) and message.content == turn_spec.user_query
                               for message in messages), f"{label}: current question changed or missing"
                if check.tool_pairs:
                    pending: set[str] = set()
                    for message in messages:
                        if isinstance(message, ToolMessage):
                            assert message.tool_call_id in pending, f"{label}: orphan tool result"
                            pending.remove(message.tool_call_id)
                        else:
                            assert not pending, f"{label}: incomplete tool results"
                            if isinstance(message, AIMessage):
                                pending = {call["id"] for call in message.tool_calls}
                    assert not pending, f"{label}: unresolved tool calls"

    totals = {
        name: sum(turn.call_deltas[name] for turn in result.turns)
        for name in ("llm", "dmf", "document_extract", "export", "watchlist")
    }
    _assert_calls(totals, case.expected_calls, f"case={case.id} totals")
