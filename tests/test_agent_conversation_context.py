from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.agent import graph as graph_module
from src.agent import nodes, tooling
from src.tools import export_tools


def _dmf_result() -> dict:
    return {
        "success": True,
        "message": "全部查询完成",
        "query": {"ingredients": ["Ibuprofen"]},
        "query_count": 1,
        "success_count": 1,
        "failed_count": 0,
        "total_records": 1,
        "results": [
            {
                "success": True,
                "query": {"ingredient": "Ibuprofen"},
                "records": [
                    {
                        "dmf_no": "12345",
                        "applicant_name": "Example Pharma",
                        "ingredient": "Ibuprofen",
                        "valid_date": "2027-01-01",
                    }
                ],
            }
        ],
    }


def _new_query_intent(state):
    query = state["user_query"]
    if query.startswith("查询"):
        return {
            "task_type": "dmf_query",
            "request_mode": "new_query",
            "requested_outputs": ["result"],
            "needs_clarification": False,
            "dmf_no": "",
            "applicant_name": "",
            "ingredients": ["Ibuprofen"],
            "messages": [HumanMessage(content=query)],
            "tool_rounds": 0,
            "max_tool_rounds": 8,
            "last_tool_batch": [],
            "tool_stop_reason": "",
            "tool_artifacts": [],
        }
    return nodes.understand_request(state)


class CrossTurnExportModel:
    def bind_tools(self, tools):
        assert [item.name for item in tools] == ["export_dmf_excel"]
        return self

    def invoke(self, messages):
        if any(isinstance(item, ToolMessage) for item in messages[-2:]):
            return AIMessage(content="导出完成。")

        latest_user_message = next(
            item.content
            for item in reversed(messages)
            if isinstance(item, HumanMessage)
        )
        if "导出" not in latest_user_message:
            return AIMessage(content="当前请求不需要后处理工具。")

        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "export_dmf_excel",
                    "args": {"filename": "cross-turn-export"},
                    "id": "export-cross-turn",
                    "type": "tool_call",
                }
            ],
        )


def test_query_then_export_reuses_active_result_without_requery(
    tmp_path,
    monkeypatch,
) -> None:
    query_calls = 0
    output_path = tmp_path / "cross-turn-export.xlsx"

    def fake_query(state):
        nonlocal query_calls
        query_calls += 1
        return {"dmf_results": _dmf_result()}

    def fake_export(result, *, filename=None, output_dir=None):
        assert result == _dmf_result()
        output_path.write_text("workbook", encoding="utf-8")
        return output_path

    monkeypatch.setattr(graph_module, "understand_request", _new_query_intent)
    monkeypatch.setattr(graph_module, "query_dmf", fake_query)
    monkeypatch.setattr(tooling, "create_apollo_llm", CrossTurnExportModel)
    monkeypatch.setattr(export_tools, "export_multi_query_result", fake_export)

    graph = graph_module.build_research_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "cross-turn"}}

    first = graph.invoke(
        {"user_query": "查询 Ibuprofen", "warnings": []},
        config=config,
    )
    assert "12345" in first["final_answer"]
    assert query_calls == 1

    completed = graph.invoke(
        {"user_query": "导出 exel", "warnings": []},
        config=config,
    )

    assert "__interrupt__" not in completed
    assert output_path.exists()
    assert str(output_path) in completed["final_answer"]
    assert "| 12345 |" not in completed["final_answer"]
    assert query_calls == 1


def test_export_without_active_result_requests_query_first() -> None:
    result = nodes.understand_request(
        {
            "user_query": "导出 Excel",
            "warnings": [],
        }
    )

    assert result["task_type"] == "dmf_post_process"
    assert result["needs_clarification"] is True
    assert "没有可导出的 DMF 查询结果" in result["clarification_question"]


def test_explicit_export_detection_supports_common_spellings() -> None:
    assert nodes._requests_excel_export("导出 Excel") is True
    assert nodes._requests_excel_export("导出 exel") is True
    assert nodes._requests_excel_export("下载 xlsx") is True
    assert nodes._requests_excel_export("解释 Excel 是什么") is False