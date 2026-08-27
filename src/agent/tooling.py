"""Reusable LangGraph nodes for dynamic tool selection and execution."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from src.agent.state import ResearchState
from src.tools.registry import ToolContext, ToolDefinition, load_builtin_tools

DEFAULT_MAX_TOOL_ROUNDS = 8


def _tool_context(state: ResearchState) -> ToolContext:
    return ToolContext(state.get("tool_context", ToolContext.GENERAL.value))


def route_after_tool_agent(state: ResearchState) -> str:
    messages = state.get("messages") or []
    last_message = messages[-1] if messages else None
    tool_calls = (
        last_message.tool_calls
        if isinstance(last_message, AIMessage)
        else []
    )
    if not tool_calls:
        return "final"
    if state.get("tool_rounds", 0) >= state.get(
        "max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS
    ):
        return "limit"
    return "execute"


def _fingerprint(call: dict[str, Any]) -> str:
    return json.dumps(
        {"name": call.get("name"), "args": call.get("args", {})},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _tool_message(
    call: dict[str, Any],
    result: Any,
    *,
    status: str = "success",
) -> ToolMessage:
    content = json.dumps(result, ensure_ascii=False, default=str)
    return ToolMessage(
        content=content,
        tool_call_id=str(call["id"]),
        name=str(call["name"]),
        status=status,
    )


def _execute_one(
    definition: ToolDefinition,
    call: dict[str, Any],
    state: ResearchState,
) -> tuple[ToolMessage, dict[str, Any]]:
    args = dict(call.get("args") or {})
    for argument_name, state_name in definition.state_arguments:
        if state_name not in state:
            error = {"success": False, "message": f"缺少工具上下文：{state_name}"}
            return _tool_message(call, error, status="error"), {
                "tool_name": call["name"],
                "tool_call_id": call["id"],
                "result": error,
            }
        args[argument_name] = state[state_name]

    try:
        result = definition.tool.invoke(args)
    except Exception as exc:
        error = {
            "success": False,
            "message": f"工具执行失败：{exc}",
            "error_type": type(exc).__name__,
        }
        return _tool_message(call, error, status="error"), {
            "tool_name": call["name"],
            "tool_call_id": call["id"],
            "result": error,
        }

    artifact = {
        "tool_name": call["name"],
        "tool_call_id": call["id"],
        "result": result,
    }
    return _tool_message(call, result), artifact


def execute_tools(state: ResearchState) -> dict[str, Any]:
    """Execute the latest AI tool-call batch."""

    messages = state.get("messages") or []
    last_message = messages[-1] if messages else None
    calls = list(last_message.tool_calls) if isinstance(last_message, AIMessage) else []
    if not calls:
        return {}

    fingerprints = [_fingerprint(call) for call in calls]
    if fingerprints == state.get("last_tool_batch", []):
        reason = "检测到模型连续重复相同工具调用，已停止执行。"
        return {
            "messages": [
                _tool_message(
                    call,
                    {"success": False, "message": reason},
                    status="error",
                )
                for call in calls
            ],
            "tool_stop_reason": reason,
        }

    registry = load_builtin_tools()
    context = _tool_context(state)
    resolved: list[tuple[dict[str, Any], ToolDefinition | None]] = []
    for call in calls:
        try:
            definition = registry.get(str(call["name"]))
            if context not in definition.contexts:
                definition = None
        except KeyError:
            definition = None
        resolved.append((call, definition))

    immediate_messages: dict[str, ToolMessage] = {}
    immediate_artifacts: dict[str, dict[str, Any]] = {}
    executable: list[tuple[dict[str, Any], ToolDefinition]] = []
    for call, definition in resolved:
        call_id = str(call["id"])
        if definition is None:
            error = {"success": False, "message": "工具未注册或不允许在当前场景使用。"}
            immediate_messages[call_id] = _tool_message(call, error, status="error")
            immediate_artifacts[call_id] = {
                "tool_name": call["name"],
                "tool_call_id": call["id"],
                "result": error,
            }
        else:
            executable.append((call, definition))

    def run(item: tuple[dict[str, Any], ToolDefinition]):
        call, definition = item
        return str(call["id"]), _execute_one(definition, call, state)

    executed: dict[str, tuple[ToolMessage, dict[str, Any]]] = {}
    parallel = len(executable) > 1 and all(
        definition.parallel_safe for _, definition in executable
    )
    if parallel:
        with ThreadPoolExecutor(max_workers=len(executable)) as executor:
            executed.update(executor.map(run, executable))
    else:
        executed.update(run(item) for item in executable)

    result_messages: list[ToolMessage] = []
    artifacts = list(state.get("tool_artifacts") or [])
    for call in calls:
        call_id = str(call["id"])
        if call_id in immediate_messages:
            result_messages.append(immediate_messages[call_id])
            artifacts.append(immediate_artifacts[call_id])
            continue
        message, artifact = executed[call_id]
        result_messages.append(message)
        artifacts.append(artifact)

    return {
        "messages": result_messages,
        "last_tool_batch": fingerprints,
        "tool_artifacts": artifacts,
        "tool_stop_reason": "",
    }


def route_after_tool_execution(state: ResearchState) -> str:
    return "final" if state.get("tool_stop_reason") else "agent"


def mark_tool_limit(state: ResearchState) -> dict[str, Any]:
    maximum = state.get("max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS)
    return {
        "tool_stop_reason": f"工具调用达到 {maximum} 轮上限，已停止执行。"
    }


def finalize_general_answer(state: ResearchState) -> dict[str, Any]:
    if state.get("tool_stop_reason"):
        return {"final_answer": state["tool_stop_reason"]}

    messages = state.get("messages") or []
    last_message = messages[-1] if messages else None
    if isinstance(last_message, AIMessage) and last_message.content:
        return {"final_answer": str(last_message.content).strip()}
    return {"final_answer": "工具调用达到限制，未能生成最终回答。"}