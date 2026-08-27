from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.agent import graph as graph_module
from src.agent import nodes, tooling
from src.schemas.intent import ResearchIntent


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
            "data_source": "dmf",
            "use_existing_data": False,
            "query_document_conditions": False,
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


class ExistingResultIntentModel:
    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        user_query = messages[-1].content
        return ResearchIntent(
            data_source="dmf",
            use_existing_data=True,
            requested_outputs=(
                ["export"] if "导出" in user_query else ["analysis"]
            ),
        )


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
    monkeypatch.setattr(nodes, "export_multi_query_result", fake_export)
    monkeypatch.setattr(nodes, "create_apollo_llm", ExistingResultIntentModel)

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


def test_query_then_analyze_result_reuses_active_result_without_requery(
    monkeypatch,
) -> None:
    query_calls = 0

    def fake_query(state):
        nonlocal query_calls
        query_calls += 1
        return {"dmf_results": _dmf_result()}

    monkeypatch.setattr(graph_module, "understand_request", _new_query_intent)
    monkeypatch.setattr(graph_module, "query_dmf", fake_query)
    monkeypatch.setattr(nodes, "_analyze_dmf_text", lambda state, result: "已有结果分析")
    monkeypatch.setattr(nodes, "create_apollo_llm", ExistingResultIntentModel)
    graph = graph_module.build_research_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "cross-turn-analysis"}}

    graph.invoke(
        {"user_query": "查询 Ibuprofen", "warnings": []},
        config=config,
    )
    analyzed = graph.invoke(
        {"user_query": "分析刚才的结果", "warnings": []},
        config=config,
    )

    assert analyzed["final_answer"] == "已有结果分析"
    assert query_calls == 1


def test_same_thread_retains_twelve_message_rounds(monkeypatch) -> None:
    monkeypatch.setattr(graph_module, "understand_request", _new_query_intent)
    monkeypatch.setattr(
        graph_module,
        "query_dmf",
        lambda state: {"dmf_results": _dmf_result()},
    )
    graph = graph_module.build_research_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "twelve-rounds"}}

    for round_number in range(1, 13):
        graph.invoke(
            {"user_query": f"查询 Ibuprofen 第 {round_number} 轮", "warnings": []},
            config=config,
        )

    messages = graph.get_state(config).values["messages"]
    user_messages = [
        message.content
        for message in messages
        if isinstance(message, HumanMessage)
    ]
    assert user_messages == [
        f"查询 Ibuprofen 第 {round_number} 轮"
        for round_number in range(1, 13)
    ]
    assert len([message for message in messages if isinstance(message, AIMessage)]) == 12


def test_intent_receives_only_the_latest_six_complete_rounds(monkeypatch) -> None:
    calls = []

    class StructuredModel:
        def invoke(self, messages):
            calls.append(messages)
            return ResearchIntent(data_source="none")

    class FakeLlm:
        def with_structured_output(self, schema):
            return StructuredModel()

    history = []
    for round_number in range(1, 9):
        history.extend(
            [
                HumanMessage(content=f"用户第 {round_number} 轮"),
                AIMessage(content=f"回复第 {round_number} 轮"),
            ]
        )
    monkeypatch.setattr(nodes, "create_apollo_llm", FakeLlm)

    nodes.understand_request(
        {"user_query": "继续", "messages": history, "warnings": []}
    )

    content = calls[0][1].content
    assert "用户第 2 轮" not in content
    assert "回复第 2 轮" not in content
    assert "用户第 3 轮" in content
    assert "回复第 8 轮" in content


def test_export_without_active_result_requests_query_first(monkeypatch) -> None:
    class StructuredModel:
        def invoke(self, messages):
            return ResearchIntent(
                data_source="dmf",
                use_existing_data=True,
                requested_outputs=["export"],
            )

    class FakeLlm:
        def with_structured_output(self, schema):
            return StructuredModel()

    monkeypatch.setattr(nodes, "create_apollo_llm", FakeLlm)
    result = nodes.understand_request(
        {
            "user_query": "导出 Excel",
            "warnings": [],
        }
    )
    assert "task_type" not in result
    assert result["data_source"] == "dmf"
    assert result["use_existing_data"] is True
    assert result["needs_clarification"] is True
    assert "没有可继续处理的 DMF 查询结果" in result["clarification_question"]