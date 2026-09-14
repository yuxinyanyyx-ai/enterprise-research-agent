from __future__ import annotations

import json
import re
from typing import Any, Callable
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from src.agent.audit_log import trace_operation, write_event
from src.agent.context_budget import ContextBudget, ContextBudgetExceeded, build_model_messages, estimate_input_tokens
from src.agent.prompts import REACT_SYSTEM_PROMPT
from src.agent.react_state import ReactState
from src.agent.domain_workflows import COMMON_INPUTS, DOCUMENT_FIELDS, WATCHLIST_FIELDS
from src.schemas.workflow import WorkflowResult
from src.agent.tooling import (
    DEFAULT_MAX_TOOL_ROUNDS,
    execute_tools,
    finalize_general_answer,
)
from src.llm.apollo import create_apollo_llm
from src.tools.registry import ToolContext, ToolRisk, load_builtin_tools


CAPABILITIES = {"search_dmf", "export_dmf_excel", "run_document_dmf_workflow", "run_watchlist_workflow"}


def export_available(state: ReactState) -> bool:
    result = state.get("dmf_results") or {}
    return bool(state.get("result_id") and isinstance(result.get("results"), list) and result.get("results"))


def function_problem(state: ReactState, call) -> str:
    name = call["name"]
    if name not in {"search_dmf", "export_dmf_excel"}:
        return "当前外层不允许该函数工具。"
    definition = load_builtin_tools().get(name)
    if ToolContext.GENERAL not in definition.contexts or (
        definition.risk is not ToolRisk.READ_ONLY and name != "export_dmf_excel"
    ):
        return "工具不允许在当前场景执行。"
    query = state.get("user_query", "").casefold()
    if name == "export_dmf_excel":
        if not re.search(r"导出|下载|export|download|excel|exel", query):
            return "只有用户明确要求导出时才能生成 Excel。"
        return "当前没有可导出的有效查询结果，请先查询。" if not export_available(state) else ""
    args = call.get("args") or {}
    values = [args.get("dmf_no", ""), args.get("applicant_name", ""), *(args.get("ingredients") or [])]
    if not any(values) or any(not isinstance(value, str) or value.casefold() not in query for value in values if value):
        return "请提供明确的查询条件；文档来源条件必须经文档 Workflow 确认。"
    return ""


def prepare_react_request(state: ReactState) -> dict[str, Any]:
    write_event("request.started", state)
    maximum = state.get("react_max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ValueError("react_max_tool_rounds must be a positive integer")
    signature = json.dumps(state.get("document_artifacts") or {}, sort_keys=True)
    invalidated = {}
    if state.get("document_signature") is not None and signature != state["document_signature"]:
        invalidated = {"document_extractions": {}, "document_errors": {}, "merged_document_query": {},
                       "domain_pending": {key: value for key, value in (state.get("domain_pending") or {}).items() if key != "document"}}
        if state.get("result_source") == "document":
            invalidated.update({"dmf_results": {}, "result_id": "", "result_source": ""})
    return {
        **invalidated, "document_signature": signature,
        "request_id": state.get("request_id") or uuid4().hex,
        "react_messages": [HumanMessage(content=state.get("user_query") or "")],
        "react_tool_context": ToolContext.GENERAL.value,
        "react_tool_rounds": 0,
        "react_max_tool_rounds": state.get(
            "react_max_tool_rounds", DEFAULT_MAX_TOOL_ROUNDS
        ),
        "react_last_tool_batch": [],
        "react_tool_stop_reason": "",
        "workflow_tool_call_id": "",
        "tool_artifacts": [],
        "final_answer": "",
        "workflow_input": {}, "workflow_output": {}, "workflow_status": "", "workflow_name": "",
        "operation_ledger": {}, "completed_workflows": [], "function_ledger": {},
    }


def build_react_agent_node(llm_factory: Callable[[], Any] = create_apollo_llm):
    def react_agent(state: ReactState) -> dict[str, Any]:
        definitions = [item for item in load_builtin_tools().for_context(ToolContext.GENERAL)
                       if item.tool.name in CAPABILITIES and item.tool.name not in state.get("completed_workflows", [])
                       and (item.tool.name != "export_dmf_excel" or export_available(state))]
        context = {
            "context_source": "checkpoint", "result_source": state.get("result_source", ""),
            "documents": [{"document_id": key, "file_name": value.get("file_name", "")} for key, value in (state.get("document_artifacts") or {}).items()],
            "extracted_conditions": state.get("merged_document_query", {}),
            "active_result_id": state.get("result_id", ""), "active_result": state.get("dmf_results", {}),
            "pending": state.get("domain_pending", {}), "completed_workflows": state.get("completed_workflows", []),
        }
        tools = [item.tool for item in definitions]
        budget = ContextBudget.from_env()
        try:
            messages = build_model_messages(state.get("react_messages") or [], context, REACT_SYSTEM_PROMPT, tools=tools, budget=budget)
        except ContextBudgetExceeded:
            write_event("context.rejected", state, reason="input_budget_exceeded")
            return {"react_messages": [AIMessage(content="当前请求或必要工具上下文过长，请缩短问题或分批提交。")],
                    "react_tool_stop_reason": "input_budget_exceeded"}
            write_event("context.prepared", state,
                    estimated_input_tokens=estimate_input_tokens(messages, [convert_to_openai_tool(tool) for tool in tools]),
                    input_budget_tokens=budget.input_tokens,
                    source_message_count=len(state.get("react_messages") or []),
                    sent_message_count=len(messages))
        with trace_operation(state, operation="react.model"):
            model = llm_factory().bind_tools(tools)
            response = model.invoke(messages)
        for call in getattr(response, "tool_calls", []):
            write_event("tool.selected", state, call=call,
                        round=state.get("react_tool_rounds", 0),
                        argument_count=len(call.get("args") or {}))
        return {"react_messages": [response]}

    return react_agent


def execute_function_tools(state: ReactState) -> dict[str, Any]:
    call = state["react_messages"][-1].tool_calls[0]
    problem = function_problem(state, call)
    if problem:
        return _reject_calls(state, problem)
    ledger = dict(state.get("function_ledger") or {})
    key = json.dumps({"name": call["name"], "args": call.get("args", {}),
                      "result_id": state.get("result_id", "") if call["name"] == "export_dmf_excel" else ""}, sort_keys=True)
    if key in ledger:
        if call["name"] == "search_dmf":
            return _reject_calls(state, "本请求已执行相同查询；多次独立查询不会自动合并，请明确当前需要的结果。")
        write_event("tool.reused", state, call=call, reason="request_deduplication")
        return {"react_messages": [ToolMessage(content=json.dumps(ledger[key], ensure_ascii=False), tool_call_id=call["id"], name=call["name"])],
                "react_tool_rounds": state.get("react_tool_rounds", 0) + 1}
    execution_state = {**state, "react_last_tool_batch": []}
    if call["name"] == "search_dmf":
        execution_state["dmf_results"] = {}
    updates = execute_tools(execution_state)
    artifacts = updates.get("tool_artifacts") or []
    artifact = next((item for item in reversed(artifacts) if item.get("tool_call_id") == call["id"]), {})
    result = artifact.get("result", {})
    ledger[key] = result
    updates["function_ledger"] = ledger
    if call["name"] == "search_dmf":
        valid = isinstance(result, dict) and isinstance(result.get("results"), list)
        updates.update({"dmf_results": result if valid else {}, "result_id": uuid4().hex if valid else "", "result_source": "query", "domain_pending": {}})
    failed = isinstance(result, dict) and result.get("success") is False and not (
        result.get("success_count", 0) or any(item.get("success") or item.get("records") for item in result.get("results", []) if isinstance(item, dict))
    )
    if failed:
        if call["name"] == "search_dmf":
            updates["result_id"] = ""
        updates["react_tool_stop_reason"] = result.get("message") or "查询或导出失败。"
    return updates


def prepare_workflow_handoff(state: ReactState) -> dict[str, Any]:
    messages = state.get("react_messages") or []
    last_message = messages[-1] if messages else None
    calls = last_message.tool_calls if isinstance(last_message, AIMessage) else []
    call = calls[0]
    write_event("workflow.handoff", state, call=call)
    domain = "document" if call["name"] == "run_document_dmf_workflow" else "watchlist"
    allowed = COMMON_INPUTS | (DOCUMENT_FIELDS if domain == "document" else WATCHLIST_FIELDS)
    payload = {key: value for key, value in state.items() if key in allowed}
    payload["pending_intent"] = (state.get("domain_pending") or {}).get(domain, {})
    payload["conversation"] = [{"role": "user" if isinstance(message, HumanMessage) else "assistant", "content": str(message.content)}
                               for message in messages if isinstance(message, (HumanMessage, AIMessage)) and message.content][-12:]
    return {
        "workflow_tool_call_id": str(call["id"]),
        "workflow_name": call["name"], "workflow_input": payload, "workflow_output": {},
        "react_tool_rounds": state.get("react_tool_rounds", 0) + 1,
    }


def finalize_workflow_handoff(state: ReactState) -> dict[str, Any]:
    output = state["workflow_output"]
    result = WorkflowResult.model_validate(output["result"])
    name = state["workflow_name"]
    write_event("workflow.returned", state, call={
        "id": state.get("workflow_tool_call_id", ""),
        "name": name,
    }, status=result.status)
    pending = {}
    pending[result.workflow_name] = output["updates"].get("pending_intent", {})
    allowed = (DOCUMENT_FIELDS - {"document_artifacts"}) if result.workflow_name == "document" else {"operation_ledger"}
    updates = {key: value for key, value in output["updates"].items() if key in allowed}
    if "dmf_results" in result.data:
        data = result.data["dmf_results"]
        updates.update({"dmf_results": data, "result_id": uuid4().hex if data.get("results") else "", "result_source": "document"})
    if "watchlist_result" in result.data:
        updates["watchlist_result"] = result.data["watchlist_result"]
    return {**updates,
        "react_messages": [
            ToolMessage(
                content=result.model_dump_json(),
                tool_call_id=state["workflow_tool_call_id"],
                name=name,
                status="success" if result.can_continue else "error",
            )
        ],
        "final_answer": "" if result.can_continue else result.message,
        "workflow_status": result.status, "domain_pending": pending,
        "completed_workflows": [*state.get("completed_workflows", []), name],
        "workflow_input": {}, "workflow_output": {},
    }


def policy_error(state: ReactState) -> dict[str, Any]:
    return _reject_calls(state, "每轮只能调用一个允许的工具，且不能重复已完成的 Workflow。")


def _reject_calls(state: ReactState, reason: str) -> dict[str, Any]:
    messages = state.get("react_messages") or []
    last_message = messages[-1] if messages else None
    calls = last_message.tool_calls if isinstance(last_message, AIMessage) else []
    for call in calls:
        write_event("tool.rejected", state, call=call, reason="policy_error")
    return {
        "react_messages": [
            ToolMessage(
                content=json.dumps({"success": False, "message": reason}, ensure_ascii=False),
                tool_call_id=str(call["id"]),
                name=str(call["name"]),
                status="error",
            )
            for call in calls
        ],
        "react_tool_rounds": state.get("react_tool_rounds", 0) + 1,
        "react_last_tool_batch": [],
        "react_tool_stop_reason": "",
    }


def finalize_react_answer(state: ReactState) -> dict[str, Any]:
    write_event("request.finished", state,
                status="stopped" if state.get("react_tool_stop_reason") else "completed")
    result = finalize_general_answer(state)
    for artifact in state.get("tool_artifacts") or []:
        data = artifact.get("result") or {}
        if artifact.get("tool_name") == "export_dmf_excel" and data.get("success") and data.get("file_path"):
            if data["file_path"] not in result["final_answer"]:
                result["final_answer"] += "\n" + data["file_path"]
    return result


def finish_workflow_request(state: ReactState) -> dict[str, Any]:
    write_event("request.finished", state, status=state.get("workflow_status"))
    return {"react_messages": [AIMessage(content=state.get("final_answer") or "操作未完成。")],
            "final_answer": state.get("final_answer") or "操作未完成。"}


def stop_at_limit(state: ReactState) -> dict[str, Any]:
    reason = f"工具调用达到 {state['react_max_tool_rounds']} 轮上限，已停止执行。"
    write_event("loop.stopped", state, reason="round_limit", round=state["react_max_tool_rounds"])
    updates = _reject_calls(state, reason)
    updates.pop("react_tool_rounds", None)
    return {**updates, "react_tool_stop_reason": reason}