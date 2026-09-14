import copy

import pytest

from src.schemas.dmf import DMFSearchResult
from tests.agent_eval.assertions import assert_case_result
from tests.agent_eval.runner import AgentEvalRunner
from tests.agent_eval.schema import AgentCase


def input_check(**updates):
    return {"max_estimated_tokens": 11904, "current_query_verbatim": True, "tool_pairs": True, **updates}


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
def test_large_result_eval_exports_all_records(cross_turn):
    records = [{"dmf_no": str(index), "ingredient": "Ibuprofen", "applicant_name": "Example Pharma",
                "raw_payload": "PRIVATE-RAW-PAYLOAD" * 50} for index in range(1000)]
    data = {"success": True, "message": "ok", "query": {"ingredients": ["Ibuprofen"]},
            "total_records": 1000, "success_count": 1,
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
    case = AgentCase.model_validate({
        "id": "CTX-LARGE-EXPORT", "description": "Full result survives model projection", "modes": ["deterministic"],
        "fixtures": {
            "search": {"kind": "llm", "value": {"tool_calls": [{"name": "search_dmf", "args": {"ingredients": ["Ibuprofen"]}, "id": "search", "type": "tool_call"}]}},
            "export": {"kind": "llm", "value": {"tool_calls": [{"name": "export_dmf_excel", "args": {}, "id": "export", "type": "tool_call"}]}},
            "done": {"kind": "llm", "value": "done"}, "data": {"kind": "dmf_result", "value": data},
            "file": {"kind": "export_result", "value": "complete.xlsx"},
        }, "turns": turns, "expected_calls": {"llm": 4 if cross_turn else 3, "dmf": 1, "export": 1},
    })
    with AgentEvalRunner() as runner:
        result = runner.run(case)
    assert_case_result(case, result)
    assert result.turns[-1].state["dmf_results"] == data
    export_event = next(event for event in result.turns[-1].call_log if event["boundary"] == "export")
    assert export_event["result"] == DMFSearchResult.model_validate(data).model_dump(mode="json")
    assert len(export_event["result"]["results"][0]["records"]) == 1000


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