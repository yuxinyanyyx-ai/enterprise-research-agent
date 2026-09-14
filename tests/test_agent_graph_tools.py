from langchain_core.messages import AIMessage

from src.agent.react_graph import build_react_graph
from tests.test_react_graph import FakeLlm
from tests.test_react_composition import call, result_data


def test_query_then_export_runs_in_order_without_additional_approval(tmp_path, monkeypatch):
    calls = []
    target = tmp_path / "export.xlsx"
    target.touch()
    monkeypatch.setattr("src.tools.dmf_tools.search_dmf_queries", lambda **kwargs: calls.append("query") or result_data())
    monkeypatch.setattr("src.tools.export_tools.export_multi_query_result", lambda *args, **kwargs: calls.append("export") or target)
    responses = [call("search_dmf", "query", ingredients=["Ibuprofen"]), call("export_dmf_excel", "export"), AIMessage(content="查询并导出完成")]
    result = build_react_graph(llm_factory=lambda: FakeLlm(responses)).invoke({"user_query": "查询 Ibuprofen 并导出"})
    assert calls == ["query", "export"]
    assert "__interrupt__" not in result
    assert str(target) in result["final_answer"]


def test_query_and_export_in_one_batch_have_zero_side_effect(monkeypatch):
    monkeypatch.setattr("src.tools.dmf_tools.search_dmf_queries", lambda **kwargs: (_ for _ in ()).throw(AssertionError("No query")))
    monkeypatch.setattr("src.tools.export_tools.export_multi_query_result", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No export")))
    batch = AIMessage(content="", tool_calls=[*call("search_dmf", "query", ingredients=["Ibuprofen"]).tool_calls, *call("export_dmf_excel", "export").tool_calls])
    responses = [batch, AIMessage(content="请按顺序执行")]
    result = build_react_graph(llm_factory=lambda: FakeLlm(responses)).invoke({"user_query": "查询 Ibuprofen 并导出", "dmf_results": result_data(), "result_id": "old"})
    assert result["tool_artifacts"] == []
    assert result["react_tool_rounds"] == 1