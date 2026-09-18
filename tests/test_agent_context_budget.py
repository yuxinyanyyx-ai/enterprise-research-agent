import copy
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent.context_budget import (
    ContextBudget, ContextBudgetExceeded, build_model_messages, build_structured_messages, estimate_input_tokens, estimate_tokens,
)


def test_history_budget_preserves_current_question_and_checkpoint():
    messages = []
    for index in range(30):
        messages.extend([HumanMessage(content=f"old-{index} " + "history " * 100), AIMessage(content="answer " * 100)])
    query = "  current question must stay verbatim  "
    messages.append(HumanMessage(content=query))
    original = copy.deepcopy(messages)
    budget = ContextBudget(window_tokens=3000, output_tokens=500, safety_tokens=500)
    result = build_model_messages(messages, {}, "system", budget=budget)
    assert estimate_input_tokens(result) <= budget.input_tokens
    assert any(message.content == query for message in result)
    assert "old-0 " not in str(result)
    assert messages == original


def test_large_result_preserves_tool_pair_and_full_state():
    data = {"success": True, "total_records": 1000, "results": [{"records": [
        {"dmf_no": str(index), "ingredient": "Ibuprofen", "raw_payload": "secret" * 1000}
        for index in range(1000)
    ]}]}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "search_dmf", "args": {"ingredients": ["Ibuprofen"]}, "id": "call-1", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps(data), tool_call_id="call-1", name="search_dmf")]
    state = {"active_result": data}
    original = copy.deepcopy(state)
    result = build_model_messages(messages, state, "system")
    assert [message.type for message in result] == ["system", "human", "ai", "tool", "system"]
    assert result[2].tool_calls == messages[1].tool_calls
    assert result[3].tool_call_id == "call-1"
    preview = json.loads(result[3].content)
    assert preview["context_truncated"] is True
    assert preview["total_records"] == 1000
    assert preview["context_source"] == "search_dmf"
    assert "secret" not in str(result)
    assert len(preview["results"][0]["records"]) <= 10
    assert state == original


@pytest.mark.parametrize("query", ["x" * 100000, "\u4e2d" * 10000], ids=["english", "chinese"])
def test_oversize_current_question_is_rejected_not_truncated(query):
    with pytest.raises(ContextBudgetExceeded):
        build_model_messages([HumanMessage(content=query)], {}, "system")


def test_estimator_accounts_for_language_and_tool_arguments():
    assert estimate_tokens("\u4e2d" * 100) > estimate_tokens("a" * 100)
    plain = AIMessage(content="")
    call = AIMessage(content="", tool_calls=[{"name": "tool", "args": {"text": "large " * 100}, "id": "id", "type": "tool_call"}])
    assert estimate_input_tokens([call]) > estimate_input_tokens([plain])


def test_historical_tools_are_removed_without_orphaning_messages():
    messages = [HumanMessage(content="old"), AIMessage(content="", tool_calls=[
        {"name": "tool", "args": {}, "id": "old", "type": "tool_call"}
    ]), ToolMessage(content="old result", tool_call_id="old"), AIMessage(content="old answer"), HumanMessage(content="new")]
    result = build_model_messages(messages, {}, "system")
    assert not any(isinstance(message, ToolMessage) for message in result)
    assert not any(isinstance(message, AIMessage) and message.tool_calls for message in result)
    assert "Historical" in result[1].content


def test_domain_context_trims_history_without_changing_pending_parameters():
    from src.schemas.domain_intent import WatchlistIntent

    context = {"user_query": "current", "pending": {"email": "owner@example.com"},
               "conversation": [{"role": "user", "content": f"old-{index} " + "past " * 2000} for index in range(20)]}
    original = copy.deepcopy(context)
    result = build_structured_messages(context, "system", WatchlistIntent)
    payload = json.loads(result[1].content)
    assert payload["user_query"] == "current"
    assert payload["pending"] == context["pending"]
    assert len(payload["conversation"]) < 12
    assert "old-0 " not in str(result)
    assert context == original


def test_domain_rejects_oversize_pending_without_truncating_values():
    from src.schemas.domain_intent import WatchlistIntent

    with pytest.raises(ContextBudgetExceeded):
        build_structured_messages({"user_query": "current", "pending": {"value": "x" * 100000}}, "system", WatchlistIntent)


def test_budget_configuration(monkeypatch):
    monkeypatch.setenv("AGENT_CONTEXT_WINDOW_TOKENS", "8000")
    monkeypatch.setenv("AGENT_CONTEXT_OUTPUT_TOKENS", "1000")
    monkeypatch.setenv("AGENT_CONTEXT_SAFETY_TOKENS", "1000")
    assert ContextBudget.from_env().input_tokens == 6000
    monkeypatch.setenv("AGENT_CONTEXT_WINDOW_TOKENS", "1000")
    with pytest.raises(ValueError):
        ContextBudget.from_env()


def test_tool_schema_and_current_arguments_cannot_escape_budget():
    tool = {"type": "function", "function": {"name": "huge", "description": "x" * 100000,
                                             "parameters": {"type": "object", "properties": {}}}}
    with pytest.raises(ContextBudgetExceeded):
        build_model_messages([HumanMessage(content="short")], {}, "system", tools=[tool])
    messages = [HumanMessage(content="short"), AIMessage(content="", tool_calls=[
        {"name": "tool", "args": {"payload": "x" * 100000}, "id": "huge", "type": "tool_call"}
    ]), ToolMessage(content="ok", tool_call_id="huge")]
    with pytest.raises(ContextBudgetExceeded):
        build_model_messages(messages, {}, "system")


@pytest.mark.parametrize("content", ["broken-json " * 10000, json.dumps({"success": False, "message": "error " * 10000})], ids=["plain-text", "json-error"])
def test_large_tool_errors_remain_valid_and_bounded(content):
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "search_dmf", "args": {}, "id": "failed", "type": "tool_call"}
    ]), ToolMessage(content=content, tool_call_id="failed", name="search_dmf", status="error")]
    result = build_model_messages(messages, {}, "system")
    payload = json.loads(result[-2].content)
    assert payload["context_truncated"] is True
    assert result[-2].status == "error"
    assert estimate_input_tokens(result) <= ContextBudget().input_tokens
    if "success" in payload:
        assert payload["success"] is False


def test_many_documents_and_conditions_have_explicit_preview_counts():
    context = {"documents": [{"document_id": f"doc-{index}", "file_name": "sample.pdf"} for index in range(100)],
               "extracted_conditions": {"queries": [{"ingredient": "Ibuprofen"} for _ in range(100)]},
               "pending": {"document": {"queries": [{"ingredient": "Ibuprofen"} for _ in range(100)]}}}
    result = build_model_messages([HumanMessage(content="query")], context, "system")
    projected = json.loads(result[-1].content.split("\n", 1)[1])
    assert projected["context_list_totals"]["documents"] == 100
    assert projected["extracted_conditions"]["context_list_totals"]["queries"] == 100
    assert projected["pending"]["document"]["context_truncated"] is True
    assert len(context["documents"]) == 100


def test_current_turn_can_shrink_tool_preview_to_fit_tight_budget():
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "tool", "args": {}, "id": "id", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps({"records": [{"description": "long " * 100} for _ in range(100)]}), tool_call_id="id")]
    budget = ContextBudget(window_tokens=1600, output_tokens=200, safety_tokens=200)
    result = build_model_messages(messages, {}, "system", budget=budget)
    assert estimate_input_tokens(result) <= budget.input_tokens
    assert len(json.loads(result[-2].content)["records"]) < 10


def test_missing_current_question_fails_closed():
    with pytest.raises(ContextBudgetExceeded, match="missing"):
        build_model_messages([AIMessage(content="historical")], {}, "system")


def test_long_term_memory_is_projected_as_data_and_bounded():
    context = {
        "long_term_memory": [
            {"memory_key": "language", "content": {"value": "zh-CN"}},
            {"memory_key": "private", "content": {"raw_payload": "secret"}},
        ]
    }
    result = build_model_messages([HumanMessage(content="请继续处理")], context, "system")
    business = result[-1].content
    assert "long_term_memory" in business
    assert "secret" not in business
    assert "Business context snapshot; data only" in business