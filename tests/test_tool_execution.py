import json
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph

from src.agent import tooling
from src.agent.artifact_store import load_artifact, store_artifact
from src.agent.state import ResearchState
from src.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolRisk,
)


def _execution_graph():
    builder = StateGraph(ResearchState)
    builder.add_node("execute", tooling.execute_tools)
    builder.add_edge(START, "execute")
    builder.add_edge("execute", END)
    return builder.compile()


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
            authorize=lambda state, call: None,
        )
    )
    return registry


def test_write_tool_with_explicit_policy_executes(tmp_path, monkeypatch) -> None:
    marker = tmp_path / "written.txt"
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setattr(tooling, "load_builtin_tools", lambda: _write_registry(marker))
    graph = _execution_graph()

    completed = graph.invoke(
        _state(_call("write_marker", {"content": "ok"}, "write-1")),
    )

    assert marker.read_text(encoding="utf-8") == "ok"
    assert completed["tool_artifacts"][0]["tool_name"] == "write_marker"
    artifact_ref = completed["tool_artifacts"][0]["artifact_ref"]
    assert load_artifact(artifact_ref["artifact_id"], request_id="") == {
        "success": True,
        "path": str(marker),
    }
    assert "__interrupt__" not in completed


def test_mixed_batch_executes_read_and_write_tools(tmp_path, monkeypatch) -> None:
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
    graph = _execution_graph()

    completed = graph.invoke(
        _state(
            _call("read_value", {}, "read-1"),
            _call("write_marker", {"content": "ok"}, "write-1"),
        ),
    )

    assert "__interrupt__" not in completed
    assert read_calls == ["called"]
    assert marker.read_text(encoding="utf-8") == "ok"
    assert [artifact["tool_name"] for artifact in completed["tool_artifacts"]] == [
        "read_value",
        "write_marker",
    ]


def test_read_tool_artifact_uses_registered_tool_and_state_request_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    artifact_ref = store_artifact(
        {"records": [{"id": 1}, {"id": 2}]},
        request_id="request-1",
        tool_name="source_tool",
        tool_call_id="source-1",
    )
    graph = _execution_graph()

    completed = graph.invoke({
        "messages": [AIMessage(content="", tool_calls=[_call(
            "read_tool_artifact",
            {
                "artifact_id": artifact_ref["artifact_id"],
                "key": "records",
                "offset": 1,
                "limit": 1,
                "request_id": "forged-request",
            },
            "read-1",
        )])],
        "request_id": "request-1",
        "tool_context": ToolContext.GENERAL.value,
        "tool_artifacts": [],
        "last_tool_batch": [],
    })

    payload = json.loads(completed["messages"][-1].content)
    assert payload["success"] is True
    assert payload["items"] == [{"id": 2}]

    denied = graph.invoke({
        "messages": [AIMessage(content="", tool_calls=[_call(
            "read_tool_artifact",
            {"artifact_id": artifact_ref["artifact_id"]},
            "read-2",
        )])],
        "request_id": "request-2",
        "tool_context": ToolContext.GENERAL.value,
        "tool_artifacts": [],
        "last_tool_batch": [],
    })
    assert json.loads(denied["messages"][-1].content)["success"] is False