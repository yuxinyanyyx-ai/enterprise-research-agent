import json
from threading import Barrier

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from src.agent import tooling
from src.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolRisk,
)


def _call(name: str, args: dict, call_id: str) -> dict:
    return {
        "name": name,
        "args": args,
        "id": call_id,
        "type": "tool_call",
    }


def _state(*calls: dict) -> dict:
    return {
        "messages": [AIMessage(content="", tool_calls=list(calls))],
        "tool_context": ToolContext.GENERAL.value,
        "tool_artifacts": [],
        "last_tool_batch": [],
    }


def _registry(*definitions: ToolDefinition) -> ToolRegistry:
    registry = ToolRegistry()
    for definition in definitions:
        registry.register(definition)
    return registry


def test_execute_read_only_tool(monkeypatch) -> None:
    @tool("add")
    def add(left: int, right: int) -> dict:
        """Add two integers."""
        return {"value": left + right}

    registry = _registry(
        ToolDefinition(
            tool=add,
            risk=ToolRisk.READ_ONLY,
            contexts=frozenset({ToolContext.GENERAL}),
        )
    )
    monkeypatch.setattr(tooling, "load_builtin_tools", lambda: registry)

    result = tooling.execute_tools(_state(_call("add", {"left": 2, "right": 3}, "1")))

    message = result["messages"][0]
    assert isinstance(message, ToolMessage)
    assert json.loads(message.content) == {"value": 5}
    assert result["tool_artifacts"][0]["result"] == {"value": 5}


def test_executor_injects_trusted_state(monkeypatch) -> None:
    @tool("record_count")
    def record_count(records: list[dict]) -> dict:
        """Count trusted records."""
        return {"count": len(records)}

    registry = _registry(
        ToolDefinition(
            tool=record_count,
            risk=ToolRisk.READ_ONLY,
            contexts=frozenset({ToolContext.GENERAL}),
            state_arguments=(("records", "trusted_records"),),
        )
    )
    monkeypatch.setattr(tooling, "load_builtin_tools", lambda: registry)
    state = _state(_call("record_count", {}, "1"))
    state["trusted_records"] = [{"id": 1}, {"id": 2}]

    result = tooling.execute_tools(state)

    assert json.loads(result["messages"][0].content) == {"count": 2}


def test_parallel_safe_batch_executes_concurrently(monkeypatch) -> None:
    barrier = Barrier(2)

    @tool("wait_for_peer")
    def wait_for_peer(value: int) -> dict:
        """Wait until the other independent call starts."""
        barrier.wait(timeout=1)
        return {"value": value}

    registry = _registry(
        ToolDefinition(
            tool=wait_for_peer,
            risk=ToolRisk.READ_ONLY,
            contexts=frozenset({ToolContext.GENERAL}),
            parallel_safe=True,
        )
    )
    monkeypatch.setattr(tooling, "load_builtin_tools", lambda: registry)

    result = tooling.execute_tools(
        _state(
            _call("wait_for_peer", {"value": 1}, "1"),
            _call("wait_for_peer", {"value": 2}, "2"),
        )
    )

    assert [json.loads(item.content)["value"] for item in result["messages"]] == [1, 2]


def test_unknown_tool_returns_error_message(monkeypatch) -> None:
    monkeypatch.setattr(tooling, "load_builtin_tools", ToolRegistry)

    result = tooling.execute_tools(_state(_call("missing", {}, "1")))

    message = result["messages"][0]
    assert message.status == "error"
    assert "未注册" in message.content


def test_repeated_batch_stops_loop(monkeypatch) -> None:
    call = _call("missing", {"value": 1}, "1")
    state = _state(call)
    state["last_tool_batch"] = [tooling._fingerprint(call)]

    result = tooling.execute_tools(state)

    assert "重复相同工具调用" in result["tool_stop_reason"]
    assert result["messages"][0].status == "error"


def test_tool_round_limit_has_explainable_reason() -> None:
    state = _state(_call("missing", {}, "1"))
    state["tool_rounds"] = 8
    state["max_tool_rounds"] = 8

    assert tooling.route_after_tool_agent(state) == "limit"
    assert "8 轮上限" in tooling.mark_tool_limit(state)["tool_stop_reason"]