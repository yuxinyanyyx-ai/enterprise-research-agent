from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.agent import tooling
from src.agent.state import ResearchState
from src.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolRisk,
)


def _approval_graph():
    builder = StateGraph(ResearchState)
    builder.add_node("execute", tooling.execute_tools)
    builder.add_edge(START, "execute")
    builder.add_edge("execute", END)
    return builder.compile(checkpointer=InMemorySaver())


def _state(*calls: dict) -> dict:
    return {
        "messages": [AIMessage(content="", tool_calls=list(calls))],
        "tool_context": ToolContext.GENERAL.value,
        "tool_artifacts": [],
        "last_tool_batch": [],
    }


def _call(name: str, args: dict, call_id: str) -> dict:
    return {
        "name": name,
        "args": args,
        "id": call_id,
        "type": "tool_call",
    }


def _write_registry(marker: Path) -> ToolRegistry:
    @tool("write_marker")
    def write_marker(content: str) -> dict:
        """Write a local marker file."""
        marker.write_text(content, encoding="utf-8")
        return {"success": True, "path": str(marker)}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            tool=write_marker,
            risk=ToolRisk.LOCAL_WRITE,
            contexts=frozenset({ToolContext.GENERAL}),
        )
    )
    return registry


def test_write_tool_pauses_then_resumes_after_approval(tmp_path, monkeypatch) -> None:
    marker = tmp_path / "approved.txt"
    monkeypatch.setattr(tooling, "load_builtin_tools", lambda: _write_registry(marker))
    graph = _approval_graph()
    config = {"configurable": {"thread_id": "approved"}}

    paused = graph.invoke(
        _state(_call("write_marker", {"content": "ok"}, "write-1")),
        config=config,
    )

    assert not marker.exists()
    interrupt_value = paused["__interrupt__"][0].value
    assert interrupt_value["calls"][0]["risk"] == ToolRisk.LOCAL_WRITE.value

    completed = graph.invoke(
        Command(resume={"approved_call_ids": ["write-1"]}),
        config=config,
    )

    assert marker.read_text(encoding="utf-8") == "ok"
    assert completed["tool_artifacts"][0]["tool_name"] == "write_marker"


def test_rejected_write_tool_does_not_create_file(tmp_path, monkeypatch) -> None:
    marker = tmp_path / "rejected.txt"
    monkeypatch.setattr(tooling, "load_builtin_tools", lambda: _write_registry(marker))
    graph = _approval_graph()
    config = {"configurable": {"thread_id": "rejected"}}

    graph.invoke(
        _state(_call("write_marker", {"content": "no"}, "write-1")),
        config=config,
    )
    completed = graph.invoke(
        Command(resume={"approved_call_ids": []}),
        config=config,
    )

    assert not marker.exists()
    assert completed["messages"][-1].status == "error"
    assert "拒绝" in completed["messages"][-1].content


def test_mixed_batch_executes_nothing_before_approval(tmp_path, monkeypatch) -> None:
    marker = tmp_path / "mixed.txt"
    read_calls: list[str] = []

    @tool("read_value")
    def read_value() -> dict:
        """Read a harmless value."""
        read_calls.append("called")
        return {"value": 1}

    registry = _write_registry(marker)
    registry.register(
        ToolDefinition(
            tool=read_value,
            risk=ToolRisk.READ_ONLY,
            contexts=frozenset({ToolContext.GENERAL}),
            parallel_safe=True,
        )
    )
    monkeypatch.setattr(tooling, "load_builtin_tools", lambda: registry)
    graph = _approval_graph()
    config = {"configurable": {"thread_id": "mixed"}}

    paused = graph.invoke(
        _state(
            _call("read_value", {}, "read-1"),
            _call("write_marker", {"content": "ok"}, "write-1"),
        ),
        config=config,
    )

    assert paused["__interrupt__"]
    assert read_calls == []
    assert not marker.exists()