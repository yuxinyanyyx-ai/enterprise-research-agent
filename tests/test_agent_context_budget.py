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


def test_evidence_projection_preserves_source_completeness_flags():
    data = {"evidence": [{
        "evidence_type": "decision", "text": "source text " * 500,
        "complete": True, "truncated": False,
    }]}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "search_pec_knowledge", "args": {}, "id": "call-1", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps(data), tool_call_id="call-1", name="search_pec_knowledge")]

    result = build_model_messages(
        messages, {}, "system",
        budget=ContextBudget(window_tokens=1600, output_tokens=200, safety_tokens=200),
    )
    evidence = json.loads(result[-2].content)["evidence"][0]

    assert evidence["complete"] is True
    assert evidence["truncated"] is False
    assert evidence["context_view"] == "preview"
    assert evidence["context_truncated"] is True


def test_small_current_tool_result_is_sent_without_projection():
    data = {"success": True, "records": [{"dmf_no": "DMF-1", "status": "active"}]}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "search_dmf", "args": {}, "id": "call-1", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps(data), tool_call_id="call-1", name="search_dmf")]

    result = build_model_messages(messages, {}, "system")

    assert json.loads(result[3].content) == data


def test_metadata_or_result_body_alone_does_not_publish_reference():
    from langchain_openai.chat_models.base import _convert_message_to_dict

    reference = {"artifact_id": "saved-but-unpublished"}
    data = {"evidence": [{"text": "fact"}], "artifact_ref": reference, "context_view": "artifact_index"}
    message = ToolMessage(content=json.dumps(data), tool_call_id="call-1", name="source",
                          additional_kwargs={"artifact_ref": reference})
    publications = {"stale": {}}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "source", "args": {}, "id": "call-1", "type": "tool_call"}
    ]), message]
    result = build_model_messages(messages, {}, "system", artifact_publications=publications)
    wire = _convert_message_to_dict(result[-2])
    assert publications == {}
    assert "additional_kwargs" not in wire
    assert "artifact_ref" not in wire
    assert wire["tool_call_id"] == "call-1"


def test_empty_compact_evidence_view_is_explicit_and_does_not_publish():
    from src.agent.context_budget import _tool_view

    data = {"evidence": [{"text": "source fact", "complete": True, "truncated": False}]}
    message = ToolMessage(content=json.dumps(data), tool_call_id="call-1", name="source",
                          additional_kwargs={"artifact_ref": {"artifact_id": "audit-only"}, "result_strategy": "inline_compact"})
    publications = {}
    preview = json.loads(_tool_view(message, 0, 64, publications).content)
    assert preview["evidence"] == []
    assert preview["context_list_totals"]["evidence"] == 1
    assert preview["context_truncated"] is True
    assert publications == {}
    assert json.loads(message.content) == data


def test_large_artifact_result_uses_reference_when_current_budget_is_tight():
    data = {"success": True, "records": [{"secret": "hidden " * 200} for _ in range(100)]}
    artifact_ref = {"artifact_id": "artifact-1", "size_bytes": 100000}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "search_dmf", "args": {}, "id": "call-1", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps(data), tool_call_id="call-1", name="search_dmf",
                    additional_kwargs={"artifact_ref": artifact_ref})]
    budget = ContextBudget(window_tokens=1600, output_tokens=200, safety_tokens=200)

    publications = {}
    result = build_model_messages(messages, {}, "system", budget=budget, artifact_publications=publications)
    preview = json.loads(result[-2].content)

    assert preview["artifact_ref"] == artifact_ref
    assert preview["artifact_available"] is True
    assert preview["context_view"] == "artifact_index"
    assert preview["read_required"] is True
    assert "hidden" not in str(preview)
    assert publications == {"artifact-1": preview["artifact_ref"]}


def test_inline_compact_result_stays_inline_when_budget_is_tight():
    data = {"success": True, "evidence": [{"text": "fact " * 200} for _ in range(100)]}
    artifact_ref = {"artifact_id": "artifact-pec", "size_bytes": 100000}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "search_pec_knowledge", "args": {}, "id": "call-pec", "type": "tool_call"}
    ]), ToolMessage(
        content=json.dumps(data), tool_call_id="call-pec", name="search_pec_knowledge",
        additional_kwargs={"artifact_ref": artifact_ref, "result_strategy": "inline_compact"},
    )]

    publications = {}
    result = build_model_messages(
        messages, {}, "system",
        budget=ContextBudget(window_tokens=1600, output_tokens=200, safety_tokens=200),
        artifact_publications=publications,
    )
    preview = json.loads(result[-2].content)

    assert preview["context_view"] == "inline_compact"
    assert "read_required" not in preview
    assert "artifact_index" not in preview.get("context_view", "")
    assert publications == {}
    assert preview["evidence"]


@pytest.mark.parametrize("unit", ["x", "\u4e2d"], ids=["ascii", "chinese"])
@pytest.mark.parametrize("tight", [False, True], ids=["normal", "tight"])
def test_pec_20kb_service_result_stays_bounded_in_model_view(unit, tight):
    from src.agent.tooling import _tool_message
    from src.pec.knowledge_service import _bounded_result

    data = _bounded_result({"success": True}, [{
        "text": unit * 30000, "evidence_type": "source_text",
        "source_file": "meeting.pptx", "location": "Slide 1",
        "complete": True, "truncated": False,
    }])
    call = {"name": "search_pec_knowledge", "args": {}, "id": "pec-1", "type": "tool_call"}
    tool_message = _tool_message(call, data, result_strategy="inline_compact",
                                 artifact_ref={"artifact_id": "audit-only"})
    messages = [HumanMessage(content="question"), AIMessage(content="", tool_calls=[call]), tool_message]
    budget = ContextBudget(window_tokens=2500, output_tokens=200, safety_tokens=200) if tight else ContextBudget()
    publications = {}
    result = build_model_messages(messages, {}, "system", budget=budget, artifact_publications=publications)
    sent = result[-2]
    payload = json.loads(sent.content)
    assert len(sent.content.encode("utf-8")) <= 20000
    assert estimate_input_tokens(result) <= budget.input_tokens
    assert sent.tool_call_id == call["id"]
    assert publications == {}
    assert "read_required" not in payload
    if not tight and unit == "x":
        assert sent.content == tool_message.content
    else:
        assert payload["context_view"] == "inline_compact"
        assert payload["context_truncated"] is True
    assert tool_message.content == json.dumps(data, ensure_ascii=False, default=str)


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