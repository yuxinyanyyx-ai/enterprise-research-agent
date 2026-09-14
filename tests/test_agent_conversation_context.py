from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.react_graph import build_react_graph
from tests.test_react_graph import FakeLlm
from tests.test_react_composition import call, result_data


def test_query_then_analysis_uses_outer_llm_and_does_not_query_again(monkeypatch):
    calls = []
    seen = []
    monkeypatch.setattr("src.tools.dmf_tools.search_dmf_queries", lambda **kwargs: calls.append(kwargs) or result_data())
    responses = [call("search_dmf", "query", ingredients=["Ibuprofen"]), AIMessage(content="123"), AIMessage(content="已有结果分析")]

    class Model(FakeLlm):
        def bind_tools(self, tools):
            assert not any("summary" in tool.name or "analysis" in tool.name for tool in tools)
            return self

        def invoke(self, messages):
            seen.append(messages)
            return self.responses.pop(0)

    graph = build_react_graph(checkpointer=InMemorySaver(), llm_factory=lambda: Model(responses))
    config = {"configurable": {"thread_id": "analysis"}}
    graph.invoke({"user_query": "查询 Ibuprofen"}, config)
    result = graph.invoke({"user_query": "分析刚才结果"}, config)
    assert result["final_answer"] == "已有结果分析"
    assert len(calls) == 1
    assert '"dmf_no": "123"' in seen[-1][-1].content


def test_same_thread_retains_twelve_conversation_rounds():
    responses = [AIMessage(content=f"回答 {index}") for index in range(12)]
    graph = build_react_graph(checkpointer=InMemorySaver(), llm_factory=lambda: FakeLlm(responses))
    config = {"configurable": {"thread_id": "conversation"}}
    for index in range(12):
        graph.invoke({"user_query": f"问题 {index}"}, config)
    messages = graph.get_state(config).values["react_messages"]
    assert [message.content for message in messages if isinstance(message, HumanMessage)] == [f"问题 {index}" for index in range(12)]
    assert len([message for message in messages if isinstance(message, AIMessage)]) == 12


def test_export_without_result_has_no_side_effect(monkeypatch):
    monkeypatch.setattr("src.tools.export_tools.export_multi_query_result", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("No export")))
    responses = [call("export_dmf_excel", "export"), AIMessage(content="请先查询")]
    result = build_react_graph(llm_factory=lambda: FakeLlm(responses)).invoke({"user_query": "导出 Excel"})
    assert result["final_answer"] == "请先查询"
    assert result["tool_artifacts"] == []