from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.agent import graph as graph_module
from src.agent import nodes


def _dmf_result() -> dict:
    return {
        "success": True,
        "message": "全部查询完成",
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


def _intent(data_source: str):
    def understand(state):
        requested_outputs = ["result"] if data_source == "dmf" else []
        if data_source == "dmf" and "导出" in state["user_query"]:
            requested_outputs.append("export")
        return {
            "data_source": data_source,
            "use_existing_data": False,
            "query_document_conditions": False,
            "requested_outputs": requested_outputs,
            "needs_clarification": False,
            "dmf_no": "",
            "applicant_name": "",
            "ingredients": ["Ibuprofen"],
            "messages": [HumanMessage(content=state["user_query"])],
            "tool_rounds": 0,
            "max_tool_rounds": 8,
            "last_tool_batch": [],
            "tool_stop_reason": "",
            "tool_artifacts": [],
        }

    return understand


class ExportToolModel:
    def bind_tools(self, tools):
        assert [item.name for item in tools] == ["export_dmf_excel"]
        return self

    def invoke(self, messages):
        if any(isinstance(item, ToolMessage) for item in messages):
            return AIMessage(content="导出处理完成。")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "export_dmf_excel",
                    "args": {"filename": "agent-export"},
                    "id": "export-1",
                    "type": "tool_call",
                }
            ],
        )


class SearchToolModel:
    def bind_tools(self, tools):
        assert {item.name for item in tools} == {
            "search_dmf",
            "extract_document_dmf_params",
        }
        return self

    def invoke(self, messages):
        if any(isinstance(item, ToolMessage) for item in messages):
            return AIMessage(content="已根据真实工具结果完成查询。")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_dmf",
                    "args": {"ingredients": ["Ibuprofen"]},
                    "id": "search-1",
                    "type": "tool_call",
                }
            ],
        )


def test_dmf_export_executes_without_approval_and_preserves_fixed_answer(
    tmp_path,
    monkeypatch,
) -> None:
    output_path = tmp_path / "agent-export.xlsx"

    def fake_export(result, *, filename=None, output_dir=None):
        assert result == _dmf_result()
        output_path.write_text("workbook", encoding="utf-8")
        return output_path

    monkeypatch.setattr(graph_module, "understand_request", _intent("dmf"))
    monkeypatch.setattr(graph_module, "query_dmf", lambda state: {"dmf_results": _dmf_result()})
    monkeypatch.setattr(nodes, "export_multi_query_result", fake_export)

    graph = graph_module.build_research_graph(checkpointer=InMemorySaver())
    completed = graph.invoke(
        {"user_query": "查询 Ibuprofen 并导出 Excel", "warnings": []},
        config={"configurable": {"thread_id": "export-direct"}},
    )

    assert "__interrupt__" not in completed
    assert "| 12345 | Example Pharma | Ibuprofen | 2027-01-01 |" in completed["final_answer"]
    assert output_path.exists()
    assert str(output_path) in completed["final_answer"]


def test_general_chat_does_not_call_registered_business_tools(monkeypatch) -> None:
    class GeneralChatModel:
        def invoke(self, messages):
            return AIMessage(content="这是普通交流回答。")

    monkeypatch.setattr(graph_module, "understand_request", _intent("none"))
    monkeypatch.setattr(nodes, "create_apollo_llm", GeneralChatModel)

    graph = graph_module.build_research_graph()
    completed = graph.invoke({"user_query": "查一下 Ibuprofen", "warnings": []})

    assert completed["final_answer"] == "这是普通交流回答。"
    assert completed["tool_artifacts"] == []


