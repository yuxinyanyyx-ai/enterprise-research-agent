from langchain_core.messages import AIMessage

from src.agent.react_graph import build_react_graph
from src.pec.tools import register_pec_tools
from src.tools.registry import ToolContext, ToolRisk, build_builtin_registry
from tests.test_react_graph import FakeLlm


def _call(name: str, call_id: str, **args):
    return AIMessage(content="", tool_calls=[{
        "name": name, "id": call_id, "args": args, "type": "tool_call",
    }])


def test_pec_provider_registers_read_only_tool() -> None:
    registry = build_builtin_registry((register_pec_tools,))
    definition = registry.get("search_pec_knowledge")
    assert definition.risk is ToolRisk.READ_ONLY
    assert ToolContext.GENERAL in definition.contexts
    assert definition.parallel_safe is True


def test_pec_tool_runs_through_outer_graph(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.pec.tools.search_pec_knowledge_service",
        lambda query: {"success": True, "context": f"PEC context for {query}"},
    )
    registry = build_builtin_registry((register_pec_tools,))
    responses = [
        _call("search_pec_knowledge", "pec-1", query="historical decision"),
        AIMessage(content="answer"),
    ]
    result = build_react_graph(
        registry=registry,
        llm_factory=lambda: FakeLlm(responses),
    ).invoke({"user_query": "historical decision"})
    assert result["tool_artifacts"][0]["result"] == {
        "success": True, "context": "PEC context for historical decision",
    }
    assert result["final_answer"] == "answer"