import copy
import json

import pytest

from src.schemas.dmf import DMFSearchResult
from tests.agent_eval.assertions import assert_case_result
from tests.agent_eval.runner import AgentEvalRunner
from tests.agent_eval.schema import AgentCase


def input_check(**updates):
    return {"max_estimated_tokens": 11904, "current_query_verbatim": True, "tool_pairs": True, **updates}


def test_small_result_eval_filters_model_input_but_keeps_checkpoint():
    data = {"success": True, "message": "ok", "total_records": 1,
            "results": [{"success": True, "query": {"ingredient": "Ibuprofen"},
                         "records": [{"dmf_no": "SAFE-001", "raw_payload": "PRIVATE-RAW",
                                      "nested": {"access_token": "PRIVATE-TOKEN"}}]}]}
    original = copy.deepcopy(data)
    case = AgentCase.model_validate({
        "id": "CTX-SAFE-SMALL", "description": "Small result safety", "modes": ["deterministic"],
        "fixtures": {
            "search": {"kind": "llm", "value": {"tool_calls": [
                {"name": "search_dmf", "args": {"ingredients": ["Ibuprofen"]}, "id": "safe-search", "type": "tool_call"}]}},
            "done": {"kind": "llm", "value": "done"},
            "data": {"kind": "dmf_result", "value": data},
        },
        "turns": [{"user_query": "Search Ibuprofen", "scripted_llm": ["search", "done"], "dmf_result": "data",
                   "expected": {"llm_inputs": [input_check(call_index=1, contains=["SAFE-001"],
                                                          not_contains=["PRIVATE-RAW", "PRIVATE-TOKEN"]) ]}}],
        "expected_calls": {"llm": 2, "dmf": 1, "export": 0},
    })
    with AgentEvalRunner() as runner:
        result = runner.run(case)
    assert_case_result(case, result)
    assert result.turns[0].state["dmf_results"] == original
    assert data == original


def test_graph_rejects_bad_tool_pair_without_second_model_call(monkeypatch):
    from langchain_core.messages import AIMessage, ToolMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from src.agent import react_graph

    data = {"success": True, "total_records": 1, "records": [{"dmf_no": "DMF-1", "raw_payload": "private"}]}
    original = copy.deepcopy(data)
    calls = []

    class Model:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            calls.append(messages)
            assert len(calls) == 1
            return AIMessage(content="", tool_calls=[{
                "name": "search_dmf", "args": {"ingredients": ["Ibuprofen"]}, "id": "expected", "type": "tool_call",
            }])

    monkeypatch.setattr(react_graph, "execute_function_tools", lambda state, registry: {
        "react_messages": [ToolMessage(content="unpaired result", tool_call_id="unexpected")],
    })
    graph = react_graph.build_react_graph(checkpointer=InMemorySaver(), llm_factory=Model)
    config = {"configurable": {"thread_id": "bad-tool-pair"}}
    result = graph.invoke({"user_query": "Search Ibuprofen", "dmf_results": data}, config)

    assert result["react_tool_stop_reason"] == "invalid_tool_pairing"
    assert len(calls) == 1
    assert result["final_answer"]
    checkpoint = graph.get_state(config).values
    assert checkpoint["dmf_results"] == original
    assert any(isinstance(message, ToolMessage) and message.tool_call_id == "unexpected"
               for message in checkpoint["react_messages"])
    assert data == original


def test_thirty_turn_eval_limits_model_history_but_keeps_checkpoint():
    case = AgentCase.model_validate({
        "id": "CTX-LONG", "description": "Thirty long turns", "modes": ["deterministic"],
        "fixtures": {"answer": {"kind": "llm", "value": "response " * 200}},
        "turns": [{"user_query": f"TURN-{index:02d} " + "past context " * 200,
                   "scripted_llm": ["answer"],
                   "expected": {"llm_inputs": [input_check(**({"not_contains": ["TURN-00"]} if index == 29 else {}))]}}
                  for index in range(30)],
        "expected_calls": {"llm": 30, "dmf": 0, "export": 0},
    })
    with AgentEvalRunner() as runner:
        result = runner.run(case)
    assert_case_result(case, result)
    assert len(result.turns[-1].state["react_messages"]) == 60


@pytest.mark.parametrize("cross_turn", [False, True], ids=["same-turn", "cross-turn"])
@pytest.mark.parametrize("record_count", [20, 1000], ids=["complete-export", "oversize-stop"])
def test_result_eval_exports_complete_fitting_result_or_stops(cross_turn, record_count):
    records = [{"dmf_no": str(index), "ingredient": "Ibuprofen", "applicant_name": "Example Pharma",
                "raw_payload": "PRIVATE-RAW-PAYLOAD" * 50} for index in range(record_count)]
    data = {"success": True, "message": "ok", "query": {"ingredients": ["Ibuprofen"]},
            "total_records": record_count, "success_count": 1,
            "results": [{"success": True, "message": "ok", "query": {"ingredient": "Ibuprofen"}, "records": records}]}
    query_turn = {"user_query": "Search Ibuprofen and export Excel", "scripted_llm": ["search"], "dmf_result": "data"}
    export_turn = {"user_query": "Export previous result to Excel", "scripted_llm": ["export", "done"], "export_result": "file"}
    if cross_turn:
        query_turn["scripted_llm"].append("done")
        turns = [query_turn, export_turn]
    else:
        query_turn["scripted_llm"].extend(export_turn["scripted_llm"])
        query_turn["export_result"] = "file"
        turns = [query_turn]
    for turn in turns:
        turn["expected"] = {"llm_inputs": [input_check(call_index=index, not_contains=["PRIVATE-RAW-PAYLOAD"])
                                           for index in range(len(turn["scripted_llm"]))]}
    oversized = record_count == 1000
    if oversized:
        for index, turn in enumerate(turns):
            turn["scripted_llm"] = ["search"] if index == 0 else []
            turn.pop("export_result", None)
            turn["expected"] = {
                "state": {"react_tool_stop_reason": "input_budget_exceeded"},
                "llm_inputs": [input_check(call_index=0)] if index == 0 else [],
                "call_deltas": {"llm": 1 if index == 0 else 0, "dmf": 1 if index == 0 else 0, "export": 0},
            }
    case = AgentCase.model_validate({
        "id": "CTX-LARGE-EXPORT", "description": "Complete input or stop with full checkpoint", "modes": ["deterministic"],
        "fixtures": {
            "search": {"kind": "llm", "value": {"tool_calls": [{"name": "search_dmf", "args": {"ingredients": ["Ibuprofen"]}, "id": "search", "type": "tool_call"}]}},
            "export": {"kind": "llm", "value": {"tool_calls": [{"name": "export_dmf_excel", "args": {}, "id": "export", "type": "tool_call"}]}},
            "done": {"kind": "llm", "value": "done"}, "data": {"kind": "dmf_result", "value": data},
            "file": {"kind": "export_result", "value": "complete.xlsx"},
        }, "turns": turns, "expected_calls": {"llm": 1 if oversized else (4 if cross_turn else 3),
                              "dmf": 1, "export": 0 if oversized else 1},
    })
    with AgentEvalRunner() as runner:
        result = runner.run(case)
    assert_case_result(case, result)
    assert result.turns[-1].state["dmf_results"] == data
    from langchain_core.messages import AIMessage, ToolMessage
    messages = result.turns[-1].state["react_messages"]
    search_call = next(message for message in messages if isinstance(message, AIMessage) and message.tool_calls)
    search_message = next(message for message in messages if isinstance(message, ToolMessage))
    assert search_call.tool_calls[0]["id"] == search_message.tool_call_id
    assert json.loads(search_message.content) == data
    if oversized:
        assert result.turns[0].state["tool_artifacts"][0]["result"] == data
        assert result.turns[0].state["published_artifact_refs"] == {}
        assert result.turns[-1].answer
        return
    from src.agent.context_budget import filter_sensitive_fields
    followup = [event for event in result.turns[0].call_log if event["boundary"] == "llm"][1]["messages"]
    assert "active_result" not in json.loads(followup[-1].content.split("\n", 1)[1])
    assert json.loads(next(message.content for message in followup if isinstance(message, ToolMessage)))["results"] == filter_sensitive_fields(data)["results"]
    if cross_turn:
        new_events = result.turns[1].call_log[len(result.turns[0].call_log):]
        restored = next(event for event in new_events if event["boundary"] == "llm")["messages"][-1]
        assert json.loads(restored.content.split("\n", 1)[1])["active_result"] == filter_sensitive_fields(data)
    export_event = next(event for event in result.turns[-1].call_log if event["boundary"] == "export")
    assert export_event["result"] == DMFSearchResult.model_validate(data).model_dump(mode="json")
    assert len(export_event["result"]["results"][0]["records"]) == record_count


@pytest.mark.parametrize("language", ["english", "chinese"])
def test_oversize_query_eval_has_no_external_calls(language):
    query = "long " * 20000 if language == "english" else "\u67e5\u8be2" * 10000
    case = AgentCase.model_validate({
        "id": "CTX-OVERSIZE", "description": "Reject rather than truncate", "modes": ["deterministic"],
        "turns": [{"user_query": query, "expected": {"state": {"react_tool_stop_reason": "input_budget_exceeded"}}}],
        "expected_calls": {"llm": 0, "dmf": 0, "export": 0, "document_extract": 0, "watchlist": 0},
    })
    with AgentEvalRunner() as runner:
        result = runner.run(case)
    assert_case_result(case, result)
    assert result.turns[0].answer
    assert result.turns[0].state["react_messages"][0].content == query


@pytest.mark.parametrize("mode", ["complete", "partial", "reader", "untrusted", "wrong-source", "document"],
                         ids=["complete", "partial", "reader", "untrusted", "wrong-source", "document"])
def test_same_turn_dedup_requires_complete_trusted_result(mode):
    from langchain_core.messages import ToolMessage
    from src.agent.react_nodes import _current_result_is_visible
    from src.agent.context_budget import filter_sensitive_fields

    data = {"success": True, "total_records": 12, "records": [{"dmf_no": str(index)} for index in range(12)]}
    artifact = {"tool_name": "search_dmf", "tool_call_id": "current", "result": copy.deepcopy(data)}
    payload = copy.deepcopy(data)
    state = {"dmf_results": data, "result_source": "query", "tool_artifacts": [artifact]}
    name = "search_dmf"
    if mode == "partial":
        payload["records"] = payload["records"][:1]
    elif mode == "reader":
        name = "read_tool_artifact"
        payload = {"read_mode": "full", "value": data}
    elif mode == "untrusted":
        state["tool_artifacts"] = []
    elif mode == "wrong-source":
        state["result_source"] = "document"
    elif mode == "document":
        name = "run_document_dmf_workflow"
        state.update(result_source="document", workflow_tool_call_id="current", workflow_name=name)
        payload = {"data": {"dmf_results": data}}
    message = ToolMessage(content=json.dumps(payload), tool_call_id="current", name=name)
    original = copy.deepcopy((state, message))
    assert _current_result_is_visible(state, [message], filter_sensitive_fields(data)) is (mode in {"complete", "document"})
    assert (state, message) == original
    assert not _current_result_is_visible(state, [], filter_sensitive_fields(data))


def test_eval_input_assertions_use_turn_local_calls_and_detect_bad_expectations():
    case = AgentCase.model_validate({
        "id": "CTX-ASSERT", "description": "Input assertion self test", "modes": ["deterministic"],
        "fixtures": {"answer": {"kind": "llm", "value": "ok"}},
        "turns": [{"user_query": text, "scripted_llm": ["answer"], "expected": {"llm_inputs": [input_check()]}}
                  for text in ["first", "second"]],
    })
    with AgentEvalRunner() as runner:
        result = runner.run(case)
    assert_case_result(case, result)
    invalid = copy.deepcopy(case)
    invalid.turns[1].expected.llm_inputs[0].max_estimated_tokens = 1
    with pytest.raises(AssertionError, match="token estimate limit"):
        assert_case_result(invalid, result)
    invalid = copy.deepcopy(case)
    invalid.turns[1].expected.llm_inputs[0].call_index = 1
    with pytest.raises(AssertionError, match="missing call"):
        assert_case_result(invalid, result)


def test_document_resume_after_long_history_keeps_current_request_and_tool_pair(tmp_path):
    from pathlib import Path
    from langchain_core.messages import AIMessage, HumanMessage
    from tests.agent_eval.loader import load_cases
    from tests.agent_eval.report import write_report
    from tests.agent_eval.schema import LLMInputExpectation

    case = next(case for case in load_cases(Path(__file__).parent / "agent_eval" / "cases", case_filter="DOC-001"))
    case = case.model_copy(deep=True)
    case.initial_state["react_messages"] = [message for index in range(30) for message in (
        HumanMessage(content=f"PRIVATE-OLD-{index} " + "past " * 1000), AIMessage(content="old answer " * 1000))]
    original_query = case.turns[0].user_query
    case.turns[0].expected.llm_inputs = [LLMInputExpectation(**input_check(not_contains=["PRIVATE-OLD-0 "]))]
    case.turns[1].expected.llm_inputs = [LLMInputExpectation(
        max_estimated_tokens=11904, tool_pairs=True, contains=[original_query], not_contains=["PRIVATE-OLD-0 "])]
    with AgentEvalRunner() as runner:
        result = runner.run(case)
    assert_case_result(case, result)
    report = write_report(result, model="scripted", output_root=tmp_path)
    for filename in ("report.json", "report.md"):
        text = (report / filename).read_text(encoding="utf-8")
        assert "PRIVATE-OLD" not in text
        assert "tool_schemas" not in text
    messages = result.turns[-1].call_log[-1]["messages"]
    assert any(isinstance(message, HumanMessage) and message.content == original_query for message in messages)