from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.agent import domain_workflows
from src.agent import nodes
from src.agent.react_graph import build_react_graph
from src.agent import tooling
from src.schemas.document_dmf import ExtractedDMFQueryBatch
from src.tools.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk


class FakeBoundModel:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses

    def invoke(self, messages):
        return self.responses.pop(0)


class FakeLlm:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses

    def bind_tools(self, tools):
        return FakeBoundModel(self.responses)


def _call(name: str, call_id: str = "call-1") -> dict:
    return {"name": name, "args": {}, "id": call_id, "type": "tool_call"}


def _registry(*definitions: ToolDefinition) -> ToolRegistry:
    registry = ToolRegistry()
    for definition in definitions:
        registry.register(definition)
    return registry


def test_explicit_single_query_uses_atomic_tool(monkeypatch) -> None:
    @tool("search_dmf")
    def search_dmf(ingredients: list[str] | None = None) -> dict:
        """Search deterministic DMF data."""
        return {"success": True, "results": [], "ingredients": ingredients or []}

    registry = _registry(
        ToolDefinition(
            tool=search_dmf,
            risk=ToolRisk.READ_ONLY,
            contexts=frozenset({ToolContext.GENERAL}),
        )
    )
    monkeypatch.setattr(tooling, "load_builtin_tools", lambda: registry)
    responses = [
        AIMessage(
            content="",
            tool_calls=[{
                "name": "search_dmf",
                "args": {"ingredients": ["Ibuprofen"]},
                "id": "search-1",
                "type": "tool_call",
            }],
        ),
        AIMessage(content="查询完成"),
    ]
    graph = build_react_graph(llm_factory=lambda: FakeLlm(responses))

    result = graph.invoke({"user_query": "查询 Ibuprofen", "warnings": []})

    assert result["final_answer"] == "查询完成"
    assert result["react_tool_rounds"] == 1
    assert result["dmf_results"]["ingredients"] == ["Ibuprofen"]


def test_mixed_atomic_and_workflow_calls_have_no_side_effect(monkeypatch) -> None:
    invoked = False

    @tool("search_dmf")
    def search_dmf() -> dict:
        """Search deterministic DMF data."""
        nonlocal invoked
        invoked = True
        return {"success": True}

    registry = _registry(
        ToolDefinition(
            tool=search_dmf,
            risk=ToolRisk.READ_ONLY,
            contexts=frozenset({ToolContext.GENERAL}),
        )
    )
    monkeypatch.setattr(tooling, "load_builtin_tools", lambda: registry)
    responses = [
        AIMessage(
            content="",
            tool_calls=[_call("search_dmf", "search-1"), _call("run_document_dmf_workflow", "flow-1")],
        ),
        AIMessage(content="已纠正"),
    ]
    graph = build_react_graph(llm_factory=lambda: FakeLlm(responses))

    result = graph.invoke({"user_query": "组合请求", "warnings": []})

    assert invoked is False
    assert result["react_tool_rounds"] == 1
    assert result["final_answer"] == "已纠正"


def test_workflow_handoff_returns_only_declared_outer_state(monkeypatch) -> None:
    def understand(state, domain):
        return {
            "needs_clarification": True,
            "clarification_question": "workflow answer",
            "messages": [AIMessage(content="inner")],
        }

    monkeypatch.setattr(domain_workflows, "_parse", understand)
    responses = [AIMessage(content="", tool_calls=[_call("run_document_dmf_workflow")])]
    graph = build_react_graph(llm_factory=lambda: FakeLlm(responses))

    result = graph.invoke({"user_query": "复杂任务", "warnings": []})

    assert result["final_answer"] == "workflow answer"
    assert result["react_messages"][-2].name == "run_document_dmf_workflow"
    assert "messages" not in result
    assert "data_source" not in result
    assert "dmf_no" not in result


def test_document_workflow_interrupt_resumes_through_outer_graph(tmp_path, monkeypatch) -> None:
    markdown = tmp_path / "document.md"
    markdown.write_text("Ibuprofen", encoding="utf-8")
    artifact = {
        "document_id": "doc-1",
        "file_name": "sample.pdf",
        "source_path": str(tmp_path / "sample.pdf"),
        "markdown_path": str(markdown),
        "status": "parsed",
    }

    def understand(state, domain):
        return {
            "data_source": "document",
            "use_existing_data": False,
            "query_document_conditions": True,
            "document_ids": [],
            "selected_document_ids": [],
            "requested_outputs": ["result"],
            "needs_clarification": False,
            "messages": [],
        }

    class Service:
        def extract_query(self, artifact):
            return ExtractedDMFQueryBatch.model_validate({"ingredients": ["Ibuprofen"]})

        def execute_confirmed_query(self, query):
            return {"success": False, "results": [], "message": "mock complete"}

    monkeypatch.setattr(domain_workflows, "_parse", understand)
    monkeypatch.setattr(nodes, "DocumentDMFService", Service)
    responses = [AIMessage(content="", tool_calls=[_call("run_document_dmf_workflow")])]
    graph = build_react_graph(
        checkpointer=InMemorySaver(),
        llm_factory=lambda: FakeLlm(responses),
    )
    config = {"configurable": {"thread_id": "outer-document"}}

    paused = graph.invoke(
        {"user_query": "按文档查询", "document_artifacts": {"doc-1": artifact}, "warnings": []},
        config=config,
    )

    assert paused["__interrupt__"][0].value["type"] == "document_query_confirmation"
    completed = graph.invoke(Command(resume={"action": "reject"}), config=config)
    assert completed["final_answer"] == "已取消使用文档条件执行 DMF 查询。"
    assert "document_query_decision" not in completed


def test_plain_answer_does_not_enter_workflow() -> None:
    responses = [AIMessage(content="普通回答")]
    graph = build_react_graph(llm_factory=lambda: FakeLlm(responses))

    result = graph.invoke({"user_query": "你好", "warnings": []})

    assert result["final_answer"] == "普通回答"