import copy
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent.context_budget import (
    ContextBudget, ContextBudgetExceeded, ToolMessagePairingError, build_model_messages, build_structured_messages, estimate_input_tokens, estimate_tokens, filter_sensitive_fields,
)


@pytest.mark.parametrize("payload", [
    {"records": [{"dmf_no": str(index)} for index in range(20)]},
    {"description": "complete text " * 60},
    {f"field_{index}": index for index in range(40)},
    {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"value": "deep fact"}}}}}}}}},
    {"evidence": [{"evidence_type": "background", "text": "first"},
                  {"evidence_type": "decision", "text": "second", "complete": False}]},
], ids=["long-list", "long-text", "many-keys", "deep-object", "evidence-order"])
def test_in_budget_business_content_has_no_projection_limits(payload):
    messages = [HumanMessage(content="  query  "), AIMessage(content="", tool_calls=[
        {"name": "source", "args": {}, "id": "full", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps(payload), tool_call_id="full", name="source")]
    context = {"active_result": payload}
    original = copy.deepcopy((messages, context))
    result = build_model_messages(messages, context, "system")
    assert json.loads(result[-2].content) == payload
    assert json.loads(result[-1].content.split("\n", 1)[1]) == context
    assert result[1].content == "  query  "
    assert (messages, context) == original


def test_visible_artifact_reference_overhead_can_reject_complete_input():
    reference = {"artifact_id": "stored", "request_id": "request", "tool_name": "source",
                 "tool_call_id": "full", "sha256": "a" * 64, "size_bytes": 20, "storage": "filesystem"}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "source", "args": {}, "id": "full", "type": "tool_call"}
    ]), ToolMessage(content='{"records": [1, 2]}', tool_call_id="full", name="source",
                    additional_kwargs={"artifact_ref": reference})]
    baseline = build_model_messages(messages, {}, "system")
    budget = ContextBudget(window_tokens=estimate_input_tokens(baseline) + 20, output_tokens=10, safety_tokens=10)
    assert build_model_messages(messages, {}, "system", budget=budget) == baseline
    original = copy.deepcopy(messages)
    context = {"artifact_refs": [reference]}
    original_context = copy.deepcopy(context)
    with pytest.raises(ContextBudgetExceeded):
        build_model_messages(messages, context, "system", budget=budget)
    assert context == original_context
    assert messages == original


def test_context_reference_does_not_overwrite_business_reference():
    reference = {"artifact_id": "stored", "tool_name": "source", "tool_call_id": "full"}
    payload = {"records": [1], "artifact_ref": {"artifact_id": "business-value"}}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "source", "args": {}, "id": "full", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps(payload), tool_call_id="full", name="source",
                    additional_kwargs={"artifact_ref": reference})]
    context = {"artifact_refs": [reference]}
    original = copy.deepcopy((messages, context))
    result = build_model_messages(messages, context, "system")
    assert json.loads(result[-2].content) == payload
    assert json.loads(result[-1].content.split("\n", 1)[1]) == context
    assert (messages, context) == original


def test_small_tool_and_business_context_share_safe_filter_without_mutation():
    data = {"success": True, "token_count": 42, "records": [{
        "dmf_no": "DMF-1", "status": "active", "description": "full business text",
        "Raw_Payload": "private-raw", "Authorization": "private-auth",
        "nested": {"accessToken": "private-token", "set-cookie": "private-cookie",
                   "source_path": "private-path", "api_key": "private-key"},
    }]}
    reference = {"artifact_id": "audit-only"}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "search_dmf", "args": {}, "id": "safe", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps(data), tool_call_id="safe", name="search_dmf", status="error",
                    additional_kwargs={"artifact_ref": reference})]
    context = {"active_result": data}
    original = copy.deepcopy((messages, context))
    result = build_model_messages(messages, context, "system")

    filtered = filter_sensitive_fields(data)
    assert json.loads(result[3].content) == filtered
    assert json.loads(result[-1].content.split("\n", 1)[1])["active_result"] == filtered
    assert filtered["records"][0]["description"] == "full business text"
    assert filtered["token_count"] == 42
    assert filtered["records"][0]["status"] == "active"
    assert "private-" not in str(result)
    assert result[3].tool_call_id == "safe"
    assert result[3].status == "error"
    assert result[3].additional_kwargs == messages[2].additional_kwargs
    assert "artifact_refs" not in json.loads(result[-1].content.split("\n", 1)[1])
    assert (messages, context) == original
    filtered["records"][0]["nested"]["new"] = "changed"
    assert (messages, context) == original


@pytest.mark.parametrize("key", [
    "raw_payload", "SOURCE_PATH", "markdownPath", "authorization", "Cookie", "Set-Cookie",
    "password", "secret", "token", "api_key", "credential", "credentials", "client_secret",
    "access_token", "refreshToken", "private-key", "verifyCode", "captcha", "captcha_code",
])
def test_sensitive_filter_handles_nested_key_aliases(key):
    data = {"rows": [{key: "private", "token_count": 2, "status": "active"}]}
    original = copy.deepcopy(data)
    assert filter_sensitive_fields(data) == {"rows": [{"token_count": 2, "status": "active"}]}
    assert data == original


def test_tool_pairs_allow_reordered_results_and_sequential_batches():
    calls = [{"name": "tool", "args": {}, "id": call_id, "type": "tool_call"} for call_id in ("one", "two")]
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=calls),
                ToolMessage(content="second", tool_call_id="two"), ToolMessage(content="first", tool_call_id="one"),
                AIMessage(content="", tool_calls=[{"name": "tool", "args": {}, "id": "three", "type": "tool_call"}]),
                ToolMessage(content="third", tool_call_id="three")]
    original = copy.deepcopy(messages)
    result = build_model_messages(messages, {}, "system")
    assert [message.tool_call_id for message in result if isinstance(message, ToolMessage)] == ["two", "one", "three"]
    assert messages == original


@pytest.mark.parametrize("content", [
    "plain tool response", "{not valid json",
    [{"type": "text", "text": "business content", "metadata": {"Authorization": "private-auth"}}],
], ids=["plain-text", "malformed-json", "content-blocks"])
def test_safe_tool_content_formats_preserve_protocol_and_source(content):
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "tool", "args": {}, "id": "formats", "type": "tool_call"}
    ]), ToolMessage(content=content, tool_call_id="formats", name="tool")]
    original = copy.deepcopy(messages)
    result = build_model_messages(messages, {}, "system")
    assert result[3].content == filter_sensitive_fields(content)
    assert result[3].tool_call_id == "formats"
    assert messages == original


def test_tool_filter_does_not_publish_untrusted_artifact():
    data = {"success": True, "records": [{"dmf_no": "DMF-1", "raw_payload": "private-raw"}]}
    message = ToolMessage(content=json.dumps(data), tool_call_id="safe", name="tool",
                          additional_kwargs={"artifact_ref": {"artifact_id": "audit-only"}})
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "tool", "args": {}, "id": "safe", "type": "tool_call"}
    ]), message]
    sent = build_model_messages(messages, {}, "system")
    result = json.loads(sent[-2].content)
    assert result["records"] == [{"dmf_no": "DMF-1"}]
    assert "context_truncated" not in result
    assert "artifact_ref" not in result
    assert "artifact_refs" not in json.loads(sent[-1].content.split("\n", 1)[1])
    assert json.loads(message.content) == data


@pytest.mark.parametrize("mode", ["orphan", "duplicate-result", "missing", "duplicate-id", "empty-id", "interrupted", "reused-id"])
def test_invalid_current_tool_pairs_are_rejected(mode):
    call = {"name": "tool", "args": {}, "id": "one", "type": "tool_call"}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[call]),
                ToolMessage(content="result", tool_call_id="one")]
    if mode == "orphan":
        messages.pop(1)
    elif mode == "duplicate-result":
        messages.append(ToolMessage(content="again", tool_call_id="one"))
    elif mode == "missing":
        messages.pop()
    elif mode == "duplicate-id":
        messages[1] = AIMessage(content="", tool_calls=[call, call])
    elif mode == "empty-id":
        messages[1] = AIMessage(content="", tool_calls=[{**call, "id": ""}])
    elif mode == "interrupted":
        messages.insert(2, AIMessage(content="premature answer"))
    else:
        messages.extend([AIMessage(content="", tool_calls=[call]), ToolMessage(content="again", tool_call_id="one")])
    original = copy.deepcopy(messages)
    with pytest.raises(ToolMessagePairingError):
        build_model_messages(messages, {}, "system")
    assert messages == original


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
    original = copy.deepcopy((state, messages))
    with pytest.raises(ContextBudgetExceeded, match="Complete current context"):
        build_model_messages(messages, state, "system")
    assert (state, messages) == original
    assert len(state["active_result"]["results"][0]["records"]) == 1000
    assert messages[1].tool_calls[0]["id"] == messages[2].tool_call_id


def test_evidence_preserves_complete_source_text_and_flags():
    data = {"evidence": [{
        "evidence_type": "decision", "text": "source text " * 500,
        "complete": True, "truncated": False,
    }]}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "search_pec_knowledge", "args": {}, "id": "call-1", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps(data), tool_call_id="call-1", name="search_pec_knowledge")]

    result = build_model_messages(messages, {}, "system")
    evidence = json.loads(result[-2].content)["evidence"][0]

    assert evidence["complete"] is True
    assert evidence["truncated"] is False
    assert evidence == data["evidence"][0]
    with pytest.raises(ContextBudgetExceeded):
        build_model_messages(messages, {}, "system",
                             budget=ContextBudget(window_tokens=1600, output_tokens=200, safety_tokens=200))


def test_small_current_tool_result_is_sent_without_projection():
    data = {"success": True, "records": [{"dmf_no": "DMF-1", "status": "active"}]}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "search_dmf", "args": {}, "id": "call-1", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps(data), tool_call_id="call-1", name="search_dmf")]

    result = build_model_messages(messages, {}, "system")

    assert json.loads(result[3].content) == data


def test_metadata_and_result_body_do_not_create_reference_context():
    from langchain_openai.chat_models.base import _convert_message_to_dict

    reference = {"artifact_id": "saved-but-unpublished"}
    data = {"evidence": [{"text": "fact"}], "artifact_ref": reference, "context_view": "artifact_index"}
    message = ToolMessage(content=json.dumps(data), tool_call_id="call-1", name="source",
                          additional_kwargs={"artifact_ref": reference})
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "source", "args": {}, "id": "call-1", "type": "tool_call"}
    ]), message]
    result = build_model_messages(messages, {}, "system")
    wire = _convert_message_to_dict(result[-2])
    assert "artifact_refs" not in json.loads(result[-1].content.split("\n", 1)[1])
    assert json.loads(wire["content"]) == data
    assert "additional_kwargs" not in wire
    assert "artifact_ref" not in wire
    assert wire["tool_call_id"] == "call-1"


def test_compact_evidence_is_preserved_and_does_not_publish():
    data = {"evidence": [{"text": "source fact", "complete": True, "truncated": False}]}
    message = ToolMessage(content=json.dumps(data), tool_call_id="call-1", name="source",
                          additional_kwargs={"artifact_ref": {"artifact_id": "audit-only"}, "result_strategy": "inline_compact"})
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "source", "args": {}, "id": "call-1", "type": "tool_call"}
    ]), message]
    result = build_model_messages(messages, {}, "system")
    payload = json.loads(result[-2].content)
    assert payload == data
    assert "artifact_refs" not in json.loads(result[-1].content.split("\n", 1)[1])
    assert json.loads(message.content) == data


def test_large_artifact_result_is_rejected_without_reference_fallback():
    data = {"success": True, "records": [{"description": "hidden " * 200} for _ in range(100)]}
    artifact_ref = {"artifact_id": "artifact-1", "size_bytes": 100000}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "search_dmf", "args": {}, "id": "call-1", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps(data), tool_call_id="call-1", name="search_dmf",
                    additional_kwargs={"artifact_ref": artifact_ref})]
    budget = ContextBudget(window_tokens=1600, output_tokens=200, safety_tokens=200)

    original = copy.deepcopy(messages)
    with pytest.raises(ContextBudgetExceeded):
        build_model_messages(messages, {}, "system", budget=budget)
    assert messages == original


def test_inline_compact_result_is_rejected_when_budget_is_tight():
    data = {"success": True, "evidence": [{"text": "fact " * 200} for _ in range(100)]}
    artifact_ref = {"artifact_id": "artifact-pec", "size_bytes": 100000}
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "search_pec_knowledge", "args": {}, "id": "call-pec", "type": "tool_call"}
    ]), ToolMessage(
        content=json.dumps(data), tool_call_id="call-pec", name="search_pec_knowledge",
        additional_kwargs={"artifact_ref": artifact_ref, "result_strategy": "inline_compact"},
    )]

    original = copy.deepcopy(messages)
    with pytest.raises(ContextBudgetExceeded):
        build_model_messages(
            messages, {}, "system",
            budget=ContextBudget(window_tokens=1600, output_tokens=200, safety_tokens=200),
        )
    assert messages == original


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
    if tight or unit != "x":
        with pytest.raises(ContextBudgetExceeded):
            build_model_messages(messages, {}, "system", budget=budget)
        assert tool_message.content == json.dumps(data, ensure_ascii=False, default=str)
        return
    result = build_model_messages(messages, {}, "system", budget=budget)
    sent = result[-2]
    payload = json.loads(sent.content)
    assert len(sent.content.encode("utf-8")) <= 20000
    assert estimate_input_tokens(result) <= budget.input_tokens
    assert sent.tool_call_id == call["id"]
    assert "artifact_refs" not in json.loads(result[-1].content.split("\n", 1)[1])
    assert "read_required" not in payload
    assert sent.content == tool_message.content
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
    original = copy.deepcopy(messages)
    with pytest.raises(ContextBudgetExceeded):
        build_model_messages(messages, {}, "system")
    assert messages == original
    assert messages[-1].status == "error"


def test_many_documents_and_conditions_are_complete_or_rejected():
    context = {"documents": [{"document_id": f"doc-{index}", "file_name": "sample.pdf"} for index in range(100)],
               "extracted_conditions": {"queries": [{"ingredient": "Ibuprofen"} for _ in range(100)]},
               "pending": {"document": {"queries": [{"ingredient": "Ibuprofen"} for _ in range(100)]}}}
    original = copy.deepcopy(context)
    result = build_model_messages([HumanMessage(content="query")], context, "system")
    assert json.loads(result[-1].content.split("\n", 1)[1]) == context
    with pytest.raises(ContextBudgetExceeded):
        build_model_messages([HumanMessage(content="query")], context, "system",
                             budget=ContextBudget(window_tokens=1600, output_tokens=200, safety_tokens=200))
    assert context == original


def test_current_turn_rejects_tool_result_instead_of_shrinking():
    messages = [HumanMessage(content="query"), AIMessage(content="", tool_calls=[
        {"name": "tool", "args": {}, "id": "id", "type": "tool_call"}
    ]), ToolMessage(content=json.dumps({"records": [{"description": "long " * 100} for _ in range(100)]}), tool_call_id="id")]
    budget = ContextBudget(window_tokens=1600, output_tokens=200, safety_tokens=200)
    original = copy.deepcopy(messages)
    with pytest.raises(ContextBudgetExceeded):
        build_model_messages(messages, {}, "system", budget=budget)
    assert messages == original


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