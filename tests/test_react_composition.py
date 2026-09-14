from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
import pytest

from src.agent import domain_workflows, nodes
from src.agent.react_graph import build_react_graph
from src.schemas.document_dmf import ExtractedDMFQueryBatch
from tests.test_react_graph import FakeLlm


def call(name, identifier, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "id": identifier, "args": args, "type": "tool_call"}])


def result_data():
    return {"success": True, "message": "ok", "query": {"ingredients": ["Ibuprofen"]},
            "query_count": 1, "success_count": 1, "total_records": 1,
            "results": [{"success": True, "message": "ok", "query": {"ingredient": "Ibuprofen"},
                         "records": [{"dmf_no": "123", "ingredient": "Ibuprofen"}]}]}


def test_document_resume_then_outer_export(tmp_path, monkeypatch):
    count = {"query": 0, "extract": 0, "export": 0}

    class Service:
        def extract_query(self, artifact):
            count["extract"] += 1
            return ExtractedDMFQueryBatch.model_validate({"ingredients": ["Ibuprofen"]})

        def execute_confirmed_query(self, query):
            count["query"] += 1
            return result_data()

    def export(result, **kwargs):
        count["export"] += 1
        assert result["results"][0]["records"][0]["dmf_no"] == "123"
        target = tmp_path / "result.xlsx"
        target.touch()
        return target

    monkeypatch.setattr(nodes, "DocumentDMFService", Service)
    monkeypatch.setattr(domain_workflows, "_parse", lambda state, domain: {
        "query_document_conditions": True, "use_existing_data": False, "needs_clarification": False,
    })
    monkeypatch.setattr("src.tools.export_tools.export_multi_query_result", export)
    responses = [call("run_document_dmf_workflow", "document"), call("export_dmf_excel", "export"), AIMessage(content="完成")]
    graph = build_react_graph(checkpointer=InMemorySaver(), llm_factory=lambda: FakeLlm(responses))
    config = {"configurable": {"thread_id": "document-export"}}
    paused = graph.invoke({"user_query": "文档查询并导出", "request_id": "request-1", "document_artifacts": {
        "doc": {"document_id": "doc", "file_name": "sample.pdf", "source_path": str(tmp_path / "sample.pdf"),
            "markdown_path": str(tmp_path / "sample.md"), "status": "parsed"},
    }}, config)
    assert paused["__interrupt__"]
    assert paused["react_tool_rounds"] == 1
    assert paused["final_answer"] == ""
    assert count == {"query": 0, "extract": 1, "export": 0}
    completed = graph.invoke(Command(resume={"action": "confirm"}), config)
    assert count == {"query": 1, "extract": 1, "export": 1}
    assert completed["react_tool_rounds"] == 2
    assert completed["result_source"] == "document"
    assert "result.xlsx" in completed["final_answer"]
    assert "messages" not in completed and "confirmed_dmf_query" not in completed
    assert [message.tool_call_id for message in completed["react_messages"] if isinstance(message, ToolMessage)] == ["document", "export"]


def test_query_then_cross_turn_export_does_not_query_twice(tmp_path, monkeypatch):
    count = []
    monkeypatch.setattr("src.tools.dmf_tools.search_dmf_queries", lambda **kwargs: count.append(kwargs) or result_data())
    target = tmp_path / "result.xlsx"
    target.touch()
    monkeypatch.setattr("src.tools.export_tools.export_multi_query_result", lambda *args, **kwargs: target)
    responses = [call("search_dmf", "query", ingredients=["Ibuprofen"]), AIMessage(content="查询完成"),
                 call("export_dmf_excel", "export"), AIMessage(content="已导出")]
    graph = build_react_graph(checkpointer=InMemorySaver(), llm_factory=lambda: FakeLlm(responses))
    config = {"configurable": {"thread_id": "cross-turn"}}
    graph.invoke({"user_query": "查询 Ibuprofen"}, config)
    result = graph.invoke({"user_query": "导出刚才结果"}, config)
    assert len(count) == 1
    assert "result.xlsx" in result["final_answer"]


def test_failed_new_query_invalidates_previous_result(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("query unavailable")
    monkeypatch.setattr("src.tools.dmf_tools.search_dmf_queries", fail)
    responses = [call("search_dmf", "query", ingredients=["Ibuprofen"])]
    result = build_react_graph(llm_factory=lambda: FakeLlm(responses)).invoke({
        "user_query": "查询 Ibuprofen 并导出", "dmf_results": result_data(), "result_id": "old",
    })
    assert result["dmf_results"] == {} and result["result_id"] == ""
    assert "query unavailable" in result["final_answer"]


def test_limit_settles_calls_without_extra_io(monkeypatch):
    count = []
    monkeypatch.setattr("src.tools.dmf_tools.search_dmf_queries", lambda **kwargs: count.append(kwargs) or result_data())
    responses = [call("search_dmf", "first", ingredients=["Ibuprofen"]), call("export_dmf_excel", "blocked")]
    result = build_react_graph(llm_factory=lambda: FakeLlm(responses)).invoke({"user_query": "查询 Ibuprofen 并导出", "react_max_tool_rounds": 1})
    assert len(count) == 1 and result["react_tool_rounds"] == 1
    assert "1 轮上限" in result["final_answer"]
    assert result["react_messages"][-1].tool_call_id == "blocked"


@pytest.mark.parametrize("maximum", [0, -1, "8", True])
def test_invalid_budget_is_rejected(maximum):
    with pytest.raises(ValueError, match="positive integer"):
        build_react_graph(llm_factory=lambda: FakeLlm([])).invoke({"user_query": "hello", "react_max_tool_rounds": maximum})


def test_document_removal_invalidates_derived_result():
    responses = [AIMessage(content="文档已清除")]
    result = build_react_graph(llm_factory=lambda: FakeLlm(responses)).invoke({
        "user_query": "当前有哪些结果", "document_artifacts": {}, "document_signature": "previous-documents",
        "result_source": "document", "result_id": "old", "dmf_results": result_data(),
        "merged_document_query": {"queries": [{"ingredients": ["Ibuprofen"]}]},
    })
    assert result["dmf_results"] == {} and result["merged_document_query"] == {}


def test_nonconsecutive_duplicate_query_does_not_replay_mismatched_data(monkeypatch):
    calls = []
    def search(**kwargs):
        calls.append(kwargs)
        result = result_data()
        result["query"] = kwargs
        return result
    monkeypatch.setattr("src.tools.dmf_tools.search_dmf_queries", search)
    responses = [call("search_dmf", "first", ingredients=["Ibuprofen"]),
                 call("search_dmf", "second", ingredients=["Naproxen"]),
                 call("search_dmf", "repeat", ingredients=["Ibuprofen"]), AIMessage(content="请明确需要哪个结果")]
    result = build_react_graph(llm_factory=lambda: FakeLlm(responses)).invoke({"user_query": "查询 Ibuprofen 和 Naproxen"})
    assert len(calls) == 2
    assert result["dmf_results"]["query"]["ingredients"] == ["Naproxen"]
    assert result["react_messages"][-2].status == "error"


def test_all_failed_subqueries_stop_without_export_eligibility(monkeypatch):
    failed = {"success": False, "message": "all failed", "success_count": 0,
              "results": [{"success": False, "records": [], "query": {"ingredient": "Ibuprofen"}}]}
    monkeypatch.setattr("src.tools.dmf_tools.search_dmf_queries", lambda **kwargs: failed)
    responses = [call("search_dmf", "failed", ingredients=["Ibuprofen"])]
    result = build_react_graph(llm_factory=lambda: FakeLlm(responses)).invoke({"user_query": "查询 Ibuprofen 并导出"})
    assert result["result_id"] == ""
    assert result["final_answer"] == "all failed"


@pytest.mark.parametrize("empty", [False, True])
def test_successful_empty_and_partial_results_remain_exportable(monkeypatch, empty):
    data = result_data()
    if empty:
        data["results"][0]["records"] = []
        data["total_records"] = 0
    else:
        data["success"] = False
        data["failed_count"] = 1
        data["results"].append({"success": False, "message": "failed", "query": {}, "records": []})
    monkeypatch.setattr("src.tools.dmf_tools.search_dmf_queries", lambda **kwargs: data)
    responses = [call("search_dmf", "query", ingredients=["Ibuprofen"]), AIMessage(content="查询状态已返回")]
    result = build_react_graph(llm_factory=lambda: FakeLlm(responses)).invoke({"user_query": "查询 Ibuprofen"})
    from src.agent.react_nodes import export_available
    assert export_available(result)