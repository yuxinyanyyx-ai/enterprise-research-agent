from __future__ import annotations

import json
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from src.agent import nodes
from src.agent.context_budget import ContextBudgetExceeded, build_structured_messages
from src.agent.state import ResearchState
from src.schemas.domain_intent import DocumentIntent, WatchlistIntent
from src.schemas.intent import ResearchIntent
from src.schemas.workflow import WorkflowResult


class WorkflowInput(TypedDict):
    workflow_input: dict[str, Any]


class WorkflowOutput(TypedDict):
    workflow_output: dict[str, Any]


class WorkflowState(ResearchState, total=False):
    workflow_input: dict[str, Any]
    workflow_output: dict[str, Any]
    operation_ledger: dict[str, Any]
    conversation: list[dict[str, str]]


COMMON_INPUTS = {"user_query", "request_id", "user_id", "pending_intent", "conversation"}
DOCUMENT_FIELDS = {"document_artifacts", "document_extractions", "document_errors", "merged_document_query"}
WATCHLIST_FIELDS = {"dmf_results", "operation_ledger"}


def _initialize(state: WorkflowState, domain: str) -> dict[str, Any]:
    allowed = COMMON_INPUTS | (DOCUMENT_FIELDS if domain == "document" else WATCHLIST_FIELDS)
    return {
        "user_query": "", "request_id": "", "user_id": "", "conversation": [],
        "pending_intent": {}, "pending_clarification_reason": "", "operation_ledger": {},
        "document_artifacts": {}, "document_extractions": {}, "document_errors": {}, "merged_document_query": {},
        **{key: value for key, value in state["workflow_input"].items() if key in allowed},
        "data_source": domain, "dmf_results": state["workflow_input"].get("dmf_results", {}) if domain == "watchlist" else {},
        "final_answer": "", "watchlist_result": {}, "needs_clarification": False,
        "confirmed_dmf_query": {}, "document_query_decision": "", "document_status": "",
        "warnings": [], "workflow_output": {},
    }


def _parse(state: WorkflowState, domain: str) -> dict[str, Any]:
    schema = DocumentIntent if domain == "document" else WatchlistIntent
    query = state.get("user_query", "")
    pending = state.get("pending_intent") or {}
    completion: dict[str, Any] = {}
    if domain == "watchlist" and pending.get("data_source") == "watchlist":
        if re.fullmatch(r"[^\s,;，；@]+@[^\s,;，；@]+(?:[ ,;，；]+[^\s,;，；@]+@[^\s,;，；@]+)*", query):
            completion["watchlist_notification_emails"] = re.split(r"[ ,;，；]+", query)
        elif query in {"每周汇总", "每周", "有变化时通知", "有更新就通知", "immediate", "weekly_digest"}:
            completion["watchlist_notification_mode"] = "weekly_digest" if query in {"每周汇总", "每周", "weekly_digest"} else "immediate"
    try:
        if completion:
            intent = schema.model_validate({**{key: value for key, value in pending.items() if key in schema.model_fields}, **completion})
        else:
            prompt = (
                "只解析当前文档任务。只提取或展示条件时 query_document_conditions=false；要求按文档查询时为 true。"
                "复用已提取条件时 use_existing_data=true。不得查询不存在的文档，不负责导出。"
                if domain == "document" else
                "只解析团队清单单项操作和通知配置。只使用用户明确指定的目标和配置；缺失值保持空/null。"
                "通知 immediate 表示有更新时后台通知，weekly_digest 表示每周后台汇总，不是立即发信。"
                "邮箱为完整替换列表，不推断邮箱。只有明显补充回答才复用待补全任务，新任务忽略旧 pending。"
            )
            context = {
                "documents": nodes._active_document_context(state),
                "results": nodes._active_dmf_context(state),
                "extracted": nodes._active_extracted_query_context(state),
                "pending": pending, "conversation": state.get("conversation", []),
                "user_query": query,
            }
            messages = build_structured_messages(context, prompt, schema)
            intent = nodes.create_apollo_llm().with_structured_output(schema).invoke(messages)
            intent = schema.model_validate(intent.model_dump() if hasattr(intent, "model_dump") else intent)
        validated = nodes._validate_intent(ResearchIntent(data_source=domain, **intent.model_dump()), state)
        return validated
    except ContextBudgetExceeded:
        return {"needs_clarification": True, "clarification_question": "当前请求或必要业务参数过长，请缩小范围或分批提交。"}
    except Exception:
        nodes.logger.exception("Domain parameter parsing failed: %s", domain)
        return {"needs_clarification": True, "clarification_question": "无法确定本次操作参数，请明确需要处理的文档或关注项。"}


def _finish(state: WorkflowState, domain: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if state.get("needs_clarification"):
        status = "rejected" if state.get("clarification_question") == nodes.WATCHLIST_HIGH_IMPACT_UNSUPPORTED else "needs_clarification"
    elif domain == "watchlist":
        status = (state.get("watchlist_result") or {}).get("status", "failed")
        data["watchlist_result"] = state.get("watchlist_result", {})
    elif state.get("document_query_decision") == "reject":
        status = "cancelled"
    elif state.get("document_query_decision") in {"confirm", "edit"}:
        result = state.get("dmf_results") or {}
        status = "completed" if result.get("success") or result.get("success_count", 0) > 0 else "failed"
        data["dmf_results"] = result
    else:
        status = "completed" if state.get("merged_document_query") else "failed"
    result = WorkflowResult(workflow_name=domain, status=status, message=state.get("final_answer", ""), data=data)
    updates = {key: state.get(key, {}) for key in (DOCUMENT_FIELDS - {"document_artifacts"})} if domain == "document" else {}
    updates.update({"pending_intent": state.get("pending_intent", {}), "pending_clarification_reason": state.get("pending_clarification_reason", "")})
    if domain == "watchlist":
        updates["operation_ledger"] = state.get("operation_ledger", {})
    return {"workflow_output": {"result": result.model_dump(), "updates": updates}}


def _execute_watchlist(state: WorkflowState) -> dict[str, Any]:
    action = state.get("watchlist_action", "")
    writes = {"add", "remove", "run", "ack", "configure_notifications"}
    ledger = dict(state.get("operation_ledger") or {})
    key = json.dumps({key: state.get(key) for key in (
        "request_id", "watchlist_action", "watchlist_id", "dmf_no", "watchlist_event_id",
        "watchlist_interval_hours", *nodes.NOTIFICATION_FIELDS,
    )}, sort_keys=True, ensure_ascii=False)
    if action in writes and ledger:
        if key in ledger:
            return ledger[key]
        return {"needs_clarification": True, "clarification_question": nodes.WATCHLIST_HIGH_IMPACT_UNSUPPORTED,
                **nodes.ask_clarification({**state, "clarification_question": nodes.WATCHLIST_HIGH_IMPACT_UNSUPPORTED})}
    updates = nodes.manage_watchlist(state)
    if action in writes and not updates.get("needs_clarification"):
        ledger[key] = updates
    return {**updates, "operation_ledger": ledger}


def _query_document(state: WorkflowState) -> dict[str, Any]:
    try:
        updates = nodes.query_confirmed_document_dmf(state)
        result = updates["dmf_results"]
        return {**updates, "final_answer": result.get("message") or "文档条件查询完成。"}
    except Exception:
        nodes.logger.exception("Confirmed document query failed")
        return {"dmf_results": {}, "final_answer": "文档条件查询失败，请稍后重试。"}


def build_document_dmf_workflow(checkpointer=None):
    builder = StateGraph(WorkflowState, input_schema=WorkflowInput, output_schema=WorkflowOutput)
    builder.add_node("initialize", lambda state: _initialize(state, "document"))
    builder.add_node("parse", lambda state: _parse(state, "document"))
    builder.add_node("clarify", nodes.ask_clarification)
    builder.add_node("extract", nodes.extract_document_query)
    builder.add_node("confirm", nodes.confirm_document_query)
    builder.add_node("query", _query_document)
    builder.add_node("review", nodes.finalize_document_review)
    builder.add_node("reject", nodes.finalize_document_rejection)
    builder.add_node("finish", lambda state: _finish(state, "document"))
    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "parse")
    builder.add_conditional_edges("parse", lambda state: "clarify" if state.get("needs_clarification") else ("confirm" if state.get("query_document_conditions") else "review") if state.get("use_existing_data") else "extract")
    builder.add_conditional_edges("extract", lambda state: "confirm" if state.get("merged_document_query") and state.get("query_document_conditions") else "review")
    builder.add_conditional_edges("confirm", lambda state: "reject" if state.get("document_query_decision") == "reject" else "query")
    for name in ("clarify", "query", "review", "reject"):
        builder.add_edge(name, "finish")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)


def build_watchlist_workflow(checkpointer=None):
    builder = StateGraph(WorkflowState, input_schema=WorkflowInput, output_schema=WorkflowOutput)
    builder.add_node("initialize", lambda state: _initialize(state, "watchlist"))
    builder.add_node("parse", lambda state: _parse(state, "watchlist"))
    builder.add_node("clarify", nodes.ask_clarification)
    builder.add_node("execute", _execute_watchlist)
    builder.add_node("finish", lambda state: _finish(state, "watchlist"))
    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "parse")
    builder.add_conditional_edges("parse", lambda state: "clarify" if state.get("needs_clarification") else "execute")
    builder.add_edge("clarify", "finish")
    builder.add_edge("execute", "finish")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer)