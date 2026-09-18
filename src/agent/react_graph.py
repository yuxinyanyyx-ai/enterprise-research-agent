from __future__ import annotations

from typing import Any, Callable

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from src.agent.domain_workflows import build_document_dmf_workflow, build_watchlist_workflow
from src.agent.react_nodes import (
    build_react_agent_node,
    execute_function_tools,
    finalize_react_answer,
    finalize_workflow_handoff,
    policy_error,
    prepare_react_request,
    prepare_workflow_handoff,
    finish_workflow_request,
    stop_at_limit,
)
from src.agent.react_state import ReactState
from src.llm.apollo import create_apollo_llm
from src.tools.registry import ToolKind, ToolRegistry, load_builtin_tools


def route_after_react_agent(state: ReactState, *, registry: ToolRegistry | None = None) -> str:
    messages = state.get("react_messages") or []
    last_message = messages[-1] if messages else None
    calls = last_message.tool_calls if isinstance(last_message, AIMessage) else []
    if not calls:
        return "final"
    if state.get("react_tool_rounds", 0) >= state.get("react_max_tool_rounds", 8):
        return "limit"
    if len(calls) != 1 or calls[0]["name"] in state.get("completed_workflows", []):
        return "policy_error"

    registry = registry if registry is not None else load_builtin_tools()
    kinds: list[ToolKind | None] = []
    for call in calls:
        try:
            kinds.append(registry.get(str(call["name"])).kind)
        except KeyError:
            kinds.append(None)
    if kinds == [ToolKind.WORKFLOW_HANDOFF] and calls[0]["name"] in {"run_document_dmf_workflow", "run_watchlist_workflow"} and not calls[0].get("args"):
        return "workflow"
    if kinds and all(kind is ToolKind.FUNCTION for kind in kinds):
        return "functions"
    return "policy_error"


def build_react_graph(
    checkpointer=None,
    *,
    llm_factory: Callable[[], Any] = create_apollo_llm,
    registry: ToolRegistry | None = None,
):
    """Build the outer ReAct graph around the existing research workflow."""

    registry = registry if registry is not None else load_builtin_tools()
    builder = StateGraph(ReactState)
    builder.add_node("prepare_react_request", prepare_react_request)
    builder.add_node("react_agent", build_react_agent_node(llm_factory, registry=registry))
    builder.add_node("execute_function_tools", lambda state: execute_function_tools(state, registry=registry))
    builder.add_node("prepare_workflow_handoff", prepare_workflow_handoff)
    builder.add_node("document_workflow", build_document_dmf_workflow())
    builder.add_node("watchlist_workflow", build_watchlist_workflow())
    builder.add_node("finalize_workflow_handoff", finalize_workflow_handoff)
    builder.add_node("policy_error", policy_error)
    builder.add_node("mark_tool_limit", stop_at_limit)
    builder.add_node("finish_workflow", finish_workflow_request)
    builder.add_node("finalize", finalize_react_answer)

    builder.add_edge(START, "prepare_react_request")
    builder.add_edge("prepare_react_request", "react_agent")
    builder.add_conditional_edges(
        "react_agent",
        lambda state: route_after_react_agent(state, registry=registry),
        {
            "final": "finalize",
            "functions": "execute_function_tools",
            "workflow": "prepare_workflow_handoff",
            "policy_error": "policy_error",
            "limit": "mark_tool_limit",
        },
    )
    builder.add_conditional_edges(
        "execute_function_tools",
        lambda state: "final" if state.get("react_tool_stop_reason") else "agent",
        {"agent": "react_agent", "final": "finalize"},
    )
    builder.add_edge("policy_error", "react_agent")
    builder.add_conditional_edges("prepare_workflow_handoff", lambda state: "document_workflow" if state["workflow_name"] == "run_document_dmf_workflow" else "watchlist_workflow")
    builder.add_edge("document_workflow", "finalize_workflow_handoff")
    builder.add_edge("watchlist_workflow", "finalize_workflow_handoff")
    builder.add_conditional_edges("finalize_workflow_handoff", lambda state: "react_agent" if state["workflow_status"] == "completed" else "finish_workflow")
    builder.add_edge("finish_workflow", END)
    builder.add_edge("mark_tool_limit", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer).with_config({"recursion_limit": 160})