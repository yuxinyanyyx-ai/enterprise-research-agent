import json
from typing import Annotated

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import InjectedToolArg, tool

from src.agent import tooling
from src.agent.react_graph import build_react_graph
from src.agent.react_nodes import execute_function_tools
from src.tools import dmf_tools, providers, registry as registry_module
from src.tools.registry import (
    ToolContext,
    ToolRisk,
    build_builtin_registry,
    register_tool,
)
from tests.test_react_composition import call, result_data
from tests.test_react_graph import FakeLlm


class RecordingLlm(FakeLlm):
    def __init__(self, responses):
        super().__init__(responses)
        self.bound_names = []

    def bind_tools(self, tools):
        self.bound_names.append({item.name for item in tools})
        return super().bind_tools(tools)


def example_provider(invocations, **policy):
    risk = policy.pop("risk", ToolRisk.READ_ONLY)
    @tool("lookup_example")
    def lookup_example(value: int) -> dict:
        """Look up a numeric example."""
        invocations.append(value)
        return {"success": True, "value": value}

    def register_example(registry):
        register_tool(
            lookup_example,
            registry=registry,
            risk=risk,
            contexts={ToolContext.GENERAL},
            **policy,
        )

    return register_example


def test_provider_only_tool_is_bound_routed_and_executed(monkeypatch):
    invocations = []
    register_example = example_provider(invocations)
    monkeypatch.setattr(providers, "BUILTIN_PROVIDERS", (register_example,))
    registry = build_builtin_registry()
    model = RecordingLlm([call("lookup_example", "lookup", value=42), AIMessage(content="done")])
    result = build_react_graph(registry=registry, llm_factory=lambda: model).invoke({"user_query": "lookup"})
    assert model.bound_names == [{"lookup_example"}, {"lookup_example"}]
    assert invocations == [42]
    assert result["react_tool_rounds"] == 1
    assert result["final_answer"] == "done"
    assert result["tool_artifacts"][0]["result"]["value"] == 42
    assert result.get("dmf_results", {}) == {}


def test_provider_builds_isolated_registries_and_rejects_duplicates():
    provider = example_provider([])
    first = build_builtin_registry((provider,))
    second = build_builtin_registry((provider,))
    assert first is not second
    assert first.get("lookup_example") is not second.get("lookup_example")
    with pytest.raises(ValueError, match="重复"):
        build_builtin_registry((provider, provider))


def test_production_registry_is_cached(monkeypatch):
    calls = []
    provider = example_provider([])

    def counted_provider(registry):
        calls.append("registered")
        provider(registry)

    monkeypatch.setattr(providers, "BUILTIN_PROVIDERS", (counted_provider,))
    monkeypatch.setattr(registry_module, "_builtin_registry", None)
    assert registry_module.load_builtin_tools() is registry_module.load_builtin_tools()
    assert calls == ["registered"]


@pytest.mark.parametrize("policy", [
    {"availability": lambda state: False},
    {"authorize": lambda state, call: "denied"},
    {"risk": ToolRisk.LOCAL_WRITE},
    {"risk": ToolRisk.EXTERNAL_WRITE},
], ids=["unavailable", "denied", "local-write", "external-write"])
def test_policy_blocks_forged_calls_in_graph_and_executor(policy):
    invocations = []
    registry = build_builtin_registry((example_provider(invocations, **policy),))
    model = RecordingLlm([call("lookup_example", "lookup", value=42), AIMessage(content="blocked")])
    result = build_react_graph(registry=registry, llm_factory=lambda: model).invoke({"user_query": "lookup"})
    assert result["react_messages"][-2].status == "error"
    if "availability" in policy:
        assert all("lookup_example" not in names for names in model.bound_names)
    direct = tooling.execute_tools({"messages": [call("lookup_example", "direct", value=42)]}, registry=registry)
    assert direct["messages"][0].status == "error"
    assert invocations == []


def test_authorized_write_uses_registration_policy():
    invocations = []
    registry = build_builtin_registry((example_provider(
        invocations, risk=ToolRisk.LOCAL_WRITE,
        authorize=lambda state, call: None if state.get("user_id") == "allowed" else "denied",
    ),))
    model = FakeLlm([call("lookup_example", "lookup", value=1), AIMessage(content="done")])
    build_react_graph(registry=registry, llm_factory=lambda: model).invoke({"user_id": "allowed", "user_query": "lookup"})
    assert invocations == [1]


@pytest.mark.parametrize("reuse", [False, True])
def test_duplicate_policy_is_not_bound_to_tool_name(reuse):
    invocations = []
    registry = build_builtin_registry((example_provider(invocations, reuse_result=reuse),))
    model = FakeLlm([
        call("lookup_example", "first", value=1), call("lookup_example", "repeat", value=1), AIMessage(content="done"),
    ])
    result = build_react_graph(registry=registry, llm_factory=lambda: model).invoke({"user_query": "lookup"})
    messages = [message for message in result["react_messages"] if isinstance(message, ToolMessage)]
    assert invocations == [1]
    assert messages[-1].status == ("success" if reuse else "error")
    assert result["react_tool_rounds"] == 2


def test_result_adapter_is_owned_by_provider():
    registry = build_builtin_registry((example_provider(
        [], result_adapter=lambda state, result: {"result_source": f"example:{result['value']}"},
    ),))
    model = FakeLlm([call("lookup_example", "lookup", value=7), AIMessage(content="done")])
    result = build_react_graph(registry=registry, llm_factory=lambda: model).invoke({"user_query": "lookup"})
    assert result["result_source"] == "example:7"


def test_schema_rejects_invalid_parameter_before_io():
    invocations = []
    registry = build_builtin_registry((example_provider(invocations),))
    model = FakeLlm([call("lookup_example", "lookup", value="invalid")])
    result = build_react_graph(registry=registry, llm_factory=lambda: model).invoke({"user_query": "lookup"})
    assert invocations == []
    assert result["tool_artifacts"][0]["result"]["error_type"] == "ValidationError"
    assert result["react_tool_stop_reason"]


@pytest.mark.parametrize("mode", ["exception", "failure", "empty", "string"])
def test_generic_result_contract(mode):
    @tool("generic_result")
    def generic_result() -> object:
        """Return a generic result."""
        if mode == "exception":
            raise RuntimeError("unavailable")
        if mode == "failure":
            return {"success": False, "message": "unavailable"}
        if mode == "empty":
            return {"success": True, "items": []}
        return "text result"

    def provider(registry):
        register_tool(generic_result, registry=registry, risk=ToolRisk.READ_ONLY, contexts={ToolContext.GENERAL})

    responses = [call("generic_result", "generic")]
    failed = mode in {"exception", "failure"}
    if not failed:
        responses.append(AIMessage(content="done"))
    result = build_react_graph(registry=build_builtin_registry((provider,)), llm_factory=lambda: FakeLlm(responses)).invoke({"user_query": "query"})
    assert bool(result["react_tool_stop_reason"]) is failed


def test_injected_state_is_hidden_trusted_and_part_of_reuse_key():
    invocations = []

    @tool("inspect_result")
    def inspect_result(data: Annotated[dict, InjectedToolArg]) -> dict:
        """Inspect trusted data."""
        invocations.append(data)
        return {"success": True, "data": data}

    def provider(registry):
        register_tool(
            inspect_result, registry=registry, risk=ToolRisk.READ_ONLY, contexts={ToolContext.GENERAL},
            state_arguments={"data": "dmf_results"}, reuse_result=True,
        )

    registry = build_builtin_registry((provider,))
    assert "data" not in inspect_result.tool_call_schema.model_fields
    state = {"dmf_results": {"value": "first"}, "react_tool_context": "general"}
    for identifier, value in (("first", "first"), ("second", "second"), ("repeat", "second")):
        state.update({"dmf_results": {"value": value}, "react_messages": [call("inspect_result", identifier, data={"forged": True})]})
        updates = execute_function_tools(state, registry=registry)
        assert json.loads(updates["react_messages"][0].content)["data"] == {"value": value}
        state.update(updates)
    assert invocations == [{"value": "first"}, {"value": "second"}]


def test_chinese_query_accepts_normalized_ingredient(monkeypatch):
    invocations = []
    monkeypatch.setattr(dmf_tools, "search_dmf_queries", lambda **kwargs: invocations.append(kwargs) or result_data())
    model = FakeLlm([call("search_dmf", "query", ingredients=[" Ibuprofen "]), AIMessage(content="done")])
    result = build_react_graph(llm_factory=lambda: model).invoke({"user_query": "查询布洛芬"})
    assert invocations == [{"dmf_no": "", "applicant_name": "", "ingredients": ["Ibuprofen"]}]
    assert result["result_id"]
    assert result["react_messages"][-2].status == "success"


@pytest.mark.parametrize("args", [{}, {"dmf_no": " ", "applicant_name": "\t", "ingredients": ["", " "]}])
def test_empty_dmf_query_rejected_before_service_and_invalidates_old_result(monkeypatch, args):
    invocations = []
    monkeypatch.setattr(dmf_tools, "search_dmf_queries", lambda **kwargs: invocations.append(kwargs))
    model = FakeLlm([call("search_dmf", "query", **args)])
    result = build_react_graph(llm_factory=lambda: model).invoke({
        "user_query": "查询", "dmf_results": result_data(), "result_id": "old",
    })
    assert invocations == []
    assert result["dmf_results"] == {} and result["result_id"] == ""
    assert "至少一个" in result["final_answer"]


@pytest.mark.parametrize("query,available", [("查询 Ibuprofen", True), ("导出 Excel", False)])
def test_export_requires_both_intent_and_active_result(monkeypatch, query, available):
    invocations = []
    monkeypatch.setattr("src.tools.export_tools.export_multi_query_result", lambda *args, **kwargs: invocations.append(args))
    state = {"user_query": query}
    if available:
        state.update({"dmf_results": result_data(), "result_id": "active"})
    model = RecordingLlm([call("export_dmf_excel", "export"), AIMessage(content="blocked")])
    result = build_react_graph(llm_factory=lambda: model).invoke(state)
    assert result["react_messages"][-2].status == "error"
    assert invocations == []
    assert ("export_dmf_excel" in model.bound_names[0]) is available