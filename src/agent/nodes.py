from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.types import interrupt

from src.agent.audit_log import trace_operation, write_event
from src.agent.state import ResearchState
from src.llm.apollo import create_apollo_llm
from src.schemas.intent import ResearchIntent
from src.schemas.document_dmf import (
    DocumentQueryDecision,
    ExtractedDMFQueryBatch,
    MergedDocumentQuery,
)
from src.services.document_dmf_service import (
    DocumentDMFError,
    DocumentDMFService,
    merge_document_queries,
)
from src.dmf_watchlist.repository import (
    WatchlistConflictError,
    WatchlistNotFoundError,
)
from src.dmf_watchlist.service import (
    DMFWatchlistService,
    WatchlistUnavailableError,
    create_watchlist_service,
)

logger = logging.getLogger(__name__)

OUTPUT_ORDER = ("result", "summary", "analysis")
REQUESTED_OUTPUT_ORDER = (*OUTPUT_ORDER, "export")
DEFAULT_CLARIFICATION = (
    "请提供需要查询的成分名称、DMF 编号或申请商名称？"
)
MISSING_RESULTS_CLARIFICATION = (
    "当前会话没有可继续处理的 DMF 查询结果，请先执行查询。"
)
MISSING_DOCUMENT_CLARIFICATION = "请先使用 /file <文件路径> 上传需要分析的文档。"
MISSING_EXTRACTED_QUERY_CLARIFICATION = (
    "当前会话没有已提取的文档查询条件，请先分析文档。"
)
AMBIGUOUS_REQUEST_CLARIFICATION = "请说明需要查询、分析还是导出哪些内容？"
WATCHLIST_TARGET_CLARIFICATION = "请提供一个明确的关注项 ID 或 DMF 编号。"
WATCHLIST_EVENT_CLARIFICATION = "请同时提供关注项和需要确认的事件 ID。"
WATCHLIST_ADD_CLARIFICATION = "请提供一个 DMF 编号，或先查询出唯一一条 DMF 记录。"
WATCHLIST_HIGH_IMPACT_UNSUPPORTED = (
    "当前仅支持团队关注清单的单项操作；批量、高影响或外发操作需要单独确认，暂未开放。"
)
WATCHLIST_DISPLAY_LIMIT = 10
NOTIFICATION_FIELDS = (
    "watchlist_notification_enabled", "watchlist_notification_emails", "watchlist_notification_mode",
)


class NotificationClarification(ValueError):
    pass


def _notification_values(state) -> dict[str, Any]:
    return {name.removeprefix("watchlist_"): state[name]
            for name in NOTIFICATION_FIELDS if state.get(name) is not None}


def _notification_summary(row) -> str:
    recipients = getattr(row, "notification_emails", []) or []
    masked = ", ".join("***@" + address.rsplit("@", 1)[-1] for address in recipients) or "未配置"
    mode = getattr(row, "notification_mode", "immediate")
    mode_text = "每周汇总" if mode == "weekly_digest" else "有变化时通知"
    enabled = "开启" if getattr(row, "notification_enabled", False) else "关闭"
    return f"通知：{enabled}；模式：{mode_text}；收件邮箱：{masked}。"


def _normalize_requested_outputs(
    default_result: bool,
    requested_outputs: list[str] | None,
) -> list[str]:
    """过滤非法/重复输出，并为 DMF 查询提供安全默认值。"""

    requested = set(requested_outputs or [])
    normalized = [name for name in REQUESTED_OUTPUT_ORDER if name in requested]

    if default_result and not normalized:
        return ["result"]

    return normalized


def _has_active_dmf_results(state: ResearchState) -> bool:
    result = state.get("dmf_results") or {}
    return bool(result.get("success") and result.get("results"))


def _active_dmf_context(state: ResearchState) -> dict[str, Any]:
    result = state.get("dmf_results") or {}
    return {
        "available": _has_active_dmf_results(state),
        "query": result.get("query", {}),
        "total_records": result.get("total_records", 0),
        "dmf_numbers": _active_dmf_numbers(state)[:WATCHLIST_DISPLAY_LIMIT],
    }


def _active_dmf_numbers(state: ResearchState) -> list[str]:
    numbers: list[str] = []
    seen: set[str] = set()
    for query_result in (state.get("dmf_results") or {}).get("results", []):
        for record in query_result.get("records") or []:
            dmf_no = str(record.get("dmf_no") or "").strip()
            key = dmf_no.casefold()
            if dmf_no and key not in seen:
                seen.add(key)
                numbers.append(dmf_no)
    return numbers


def _is_high_impact_watchlist_request(user_query: str) -> bool:
    normalized = "".join(user_query.lower().split())
    return any(
        phrase in normalized
        for phrase in (
            "清空关注",
            "清空清单",
            "全部删除",
            "删除全部",
            "删除所有",
            "批量删除",
            "批量关注",
            "全部关注",
            "批量确认",
            "确认全部",
            "确认所有",
            "发送事件",
            "推送事件",
            "外发事件",
        )
    )


def _notification_request_problem(user_query: str) -> str:
    normalized = "".join(user_query.lower().split())
    if re.search(r"批量|全部|所有|allwatchlists|bulk|转发|一次性|立即发|马上发|现在发|sendnow|forward", normalized):
        return WATCHLIST_HIGH_IMPACT_UNSUPPORTED
    if re.search(r"(追加|新增|添加|删除|移除).*(邮箱|收件人)|另一个邮箱|再加|append|remove.*recipient", normalized):
        return "请提供该关注项完整的新收件邮箱列表；当前不支持追加或移除部分收件人。"
    return ""


def _active_document_context(state: ResearchState) -> dict[str, Any]:
    artifacts = state.get("document_artifacts") or {}
    return {
        "available": bool(artifacts),
        "documents": [
            {
                "document_id": document_id,
                "file_name": artifact.get("file_name", ""),
                "status": artifact.get("status", ""),
                "extracted": document_id in (state.get("document_extractions") or {}),
            }
            for document_id, artifact in artifacts.items()
        ],
    }


def _active_extracted_query_context(state: ResearchState) -> dict[str, Any]:
    queries = (state.get("merged_document_query") or {}).get("queries") or []
    return {
        "available": bool(queries),
        "query_count": len(queries),
        "preview": queries[:3],
    }




def _answer(content: str, **updates: Any) -> dict[str, Any]:
    answer = content.strip()
    return {
        **updates,
        "final_answer": answer,
        "messages": [AIMessage(content=answer)],
    }




def _validate_intent(
    intent: ResearchIntent,
    state: ResearchState,
) -> dict[str, Any]:
    """Validate semantic intent against trusted state without changing it."""

    executes_new_query = (
        intent.data_source == "dmf" and not intent.use_existing_data
    ) or (
        intent.data_source == "document"
        and intent.query_document_conditions
    )
    requested_outputs = _normalize_requested_outputs(
        executes_new_query,
        intent.requested_outputs,
    )
    if executes_new_query and "export" in requested_outputs and not any(
        output in requested_outputs for output in OUTPUT_ORDER
    ):
        requested_outputs = ["result", *requested_outputs]
    base: dict[str, Any] = {
        "data_source": intent.data_source,
        "use_existing_data": intent.use_existing_data,
        "query_document_conditions": intent.query_document_conditions,
        "document_ids": intent.document_ids,
        "selected_document_ids": intent.document_ids,
        "requested_outputs": requested_outputs,
        "needs_clarification": False,
        "clarification_question": "",
        "dmf_no": intent.dmf_no,
        "applicant_name": intent.applicant_name,
        "ingredients": intent.ingredients,
        "watchlist_action": intent.watchlist_action or "",
        "watchlist_id": intent.watchlist_id,
        "watchlist_event_id": intent.watchlist_event_id,
        "watchlist_interval_hours": intent.watchlist_interval_hours,
        **{name: getattr(intent, name) if intent.data_source == "watchlist" else None for name in NOTIFICATION_FIELDS},
        "watchlist_result": {},
        "pending_intent": {},
        "pending_clarification_reason": "",
    }

    if intent.data_source == "watchlist" and (
        intent.watchlist_action == "configure_notifications" or _notification_values(base)
    ):
        problem = _notification_request_problem(state.get("user_query") or "")
        if problem:
            return {**base, "needs_clarification": True, "clarification_question": problem}

    if intent.needs_clarification:
        return {
            **base,
            "needs_clarification": True,
            "clarification_question": AMBIGUOUS_REQUEST_CLARIFICATION,
        }

    if intent.data_source == "none":
        return base

    if intent.data_source == "watchlist":
        if _is_high_impact_watchlist_request(state.get("user_query") or ""):
            return {
                **base,
                "needs_clarification": True,
                "clarification_question": WATCHLIST_HIGH_IMPACT_UNSUPPORTED,
            }
        if (
            intent.watchlist_interval_hours is not None
            and not 1 <= intent.watchlist_interval_hours <= 168
        ):
            return {
                **base,
                "needs_clarification": True,
                "clarification_question": "关注项检查间隔必须在 1 到 168 小时之间。",
            }
        action = intent.watchlist_action
        if action == "configure_notifications" or _notification_values(base):
            problem = _notification_request_problem(state.get("user_query") or "")
            if problem:
                return {**base, "needs_clarification": True, "clarification_question": problem}
            if action not in {"add", "configure_notifications", "list"}:
                return {**base, "needs_clarification": True,
                        "clarification_question": "请单独说明要配置哪个关注项的通知。"}
            if action == "configure_notifications" and not _notification_values(base):
                return {**base, "needs_clarification": True,
                        "clarification_question": "请提供要修改的通知开关、完整邮箱列表或通知模式。"}
        if action is None:
            return {
                **base,
                "needs_clarification": True,
                "clarification_question": "请说明要添加、删除、查看还是检查团队关注项。",
            }
        if action == "list":
            return base
        if action == "add":
            if intent.dmf_no:
                return base
            references_result = any(word in (state.get("user_query") or "") for word in ("这个", "刚才", "上述", "结果"))
            candidates = _active_dmf_numbers(state) if references_result else []
            if len(candidates) == 1:
                return {**base, "dmf_no": candidates[0]}
            return {
                **base,
                "needs_clarification": True,
                "clarification_question": WATCHLIST_ADD_CLARIFICATION,
            }
        if not (intent.watchlist_id or intent.dmf_no):
            if action == "configure_notifications" and any(
                word in (state.get("user_query") or "") for word in ("这个", "刚才", "上述")
            ):
                candidates = _active_dmf_numbers(state) if _has_active_dmf_results(state) else []
                if len(candidates) == 1:
                    return {**base, "dmf_no": candidates[0]}
            return {
                **base,
                "needs_clarification": True,
                "clarification_question": WATCHLIST_TARGET_CLARIFICATION,
            }
        if action == "ack" and not intent.watchlist_event_id:
            return {
                **base,
                "needs_clarification": True,
                "clarification_question": WATCHLIST_EVENT_CLARIFICATION,
            }
        return base

    if intent.data_source == "dmf":
        if intent.use_existing_data:
            if not _has_active_dmf_results(state):
                return {
                    **base,
                    "needs_clarification": True,
                    "clarification_question": MISSING_RESULTS_CLARIFICATION,
                }
            return base

        has_query = bool(intent.dmf_no or intent.applicant_name or intent.ingredients)
        if not has_query:
            return {
                **base,
                "needs_clarification": True,
                "clarification_question": DEFAULT_CLARIFICATION,
            }
        return base

    artifacts = state.get("document_artifacts") or {}
    unknown_ids = [item for item in intent.document_ids if item not in artifacts]
    if unknown_ids:
        return {
            **base,
            "needs_clarification": True,
            "clarification_question": "指定的文档不存在或已被删除，请重新选择当前文档。",
        }

    if intent.use_existing_data:
        if not _active_extracted_query_context(state)["available"]:
            return {
                **base,
                "needs_clarification": True,
                "clarification_question": MISSING_EXTRACTED_QUERY_CLARIFICATION,
            }
        return base

    if not artifacts:
        return {
            **base,
            "needs_clarification": True,
            "clarification_question": MISSING_DOCUMENT_CLARIFICATION,
        }
    return base




def extract_document_query(state: ResearchState) -> dict[str, Any]:
    """Extract selected documents independently and merge their conditions."""

    artifacts = state.get("document_artifacts") or {}
    selected_ids = state.get("selected_document_ids") or list(artifacts)
    extractions = dict(state.get("document_extractions") or {})
    errors = dict(state.get("document_errors") or {})
    service = DocumentDMFService()
    for document_id in selected_ids:
        if document_id in extractions or document_id not in artifacts:
            continue
        try:
            extractions[document_id] = service.extract_query(
                artifacts[document_id]
            ).model_dump()
            errors.pop(document_id, None)
        except DocumentDMFError as exc:
            errors[document_id] = str(exc)

    try:
        merged = merge_document_queries(artifacts, extractions, selected_ids)
    except DocumentDMFError as exc:
        return {
            "document_status": "failed",
            "document_extractions": extractions,
            "document_errors": errors or {"workflow": str(exc)},
            "merged_document_query": {},
        }
    return {
        "document_status": "extracted",
        "document_extractions": extractions,
        "document_errors": errors,
        "merged_document_query": merged.model_dump(),
    }


def confirm_document_query(state: ResearchState) -> dict[str, Any]:
    """Pause before a real DMF query and accept confirmed or edited conditions."""

    merged = MergedDocumentQuery.model_validate(state["merged_document_query"])
    candidate = merged.to_query_batch()
    confirmation_query = merged.model_dump()
    for query in confirmation_query["queries"]:
        query["broad_query"] = bool(
            query.get("applicant_name")
            and not query.get("dmf_no")
            and not query.get("ingredients")
        )
    write_event("document.confirmation_reached", state,
                operation="confirm_document_query")
    resumed = interrupt(
        {
            "type": "document_query_confirmation",
            "message": "请确认或修改从文档中提取的 DMF 查询条件。",
            "file_names": list(dict.fromkeys(
                source.file_name
                for query in merged.queries
                for source in query.sources
            )),
            "query": confirmation_query,
            "warnings": [
                {"document_id": document_id, "message": message}
                for document_id, message in (state.get("document_errors") or {}).items()
            ],
        }
    )
    payload = resumed if isinstance(resumed, dict) else {"action": "reject"}
    action = str(payload.get("action", "reject"))
    query_payload = payload.get("query") or candidate.model_dump()
    decision = DocumentQueryDecision.model_validate(
        {"action": action, "query": None if action == "reject" else query_payload}
    )
    write_event("document.resumed", state, operation="confirm_document_query",
                decision=decision.action)
    return {
        "document_query_decision": decision.action,
        "confirmed_dmf_query": (
            decision.query.model_dump() if decision.query is not None else {}
        ),
    }


def query_confirmed_document_dmf(state: ResearchState) -> dict[str, Any]:
    """Run a DMF query only after document conditions were confirmed."""

    query = ExtractedDMFQueryBatch.model_validate(state["confirmed_dmf_query"])
    with trace_operation(state, operation="query_confirmed_document_dmf") as outcome:
        result = DocumentDMFService().execute_confirmed_query(query)
        if result.get("success") is False:
            outcome["status"] = "business_failure"
    return {
        "dmf_no": "",
        "applicant_name": "",
        "ingredients": [
            ingredient
            for item in query.queries
            for ingredient in item.ingredients
        ],
        "dmf_results": result,
    }


def finalize_document_review(state: ResearchState) -> dict[str, Any]:
    """Present extracted conditions without performing a DMF query."""

    if not state.get("merged_document_query"):
        errors = state.get("document_errors") or {}
        detail = "；".join(errors.values()) or "未提取到可用条件"
        return _answer(f"文档分析失败：{detail}")
    merged = MergedDocumentQuery.model_validate(state["merged_document_query"])
    lines = []
    for index, query in enumerate(merged.queries, start=1):
        ingredients = "、".join(query.ingredients) or "未提取到"
        sources = "、".join(source.file_name for source in query.sources)
        lines.append(
            f"{index}. DMF 编号：{query.dmf_no or '未提取到'}；"
            f"申请商：{query.applicant_name or '未提取到'}；"
            f"成分：{ingredients}；来源：{sources}"
        )
    return _answer(
        f"已从文档中提取 {len(merged.queries)} 组 DMF 查询条件：\n"
        + "\n".join(lines)
    )


def finalize_document_rejection(state: ResearchState) -> dict[str, Any]:
    return _answer("已取消使用文档条件执行 DMF 查询。")



def ask_clarification(state: ResearchState) -> dict[str, Any]:
    if state.get("clarification_question") == WATCHLIST_HIGH_IMPACT_UNSUPPORTED:
        return _answer(WATCHLIST_HIGH_IMPACT_UNSUPPORTED, pending_intent={}, pending_clarification_reason="")
    return _answer(
        state.get("clarification_question") or AMBIGUOUS_REQUEST_CLARIFICATION,
        pending_intent={
            key: state.get(key)
            for key in (
                "data_source",
                "use_existing_data",
                "query_document_conditions",
                "document_ids",
                "requested_outputs",
                "dmf_no",
                "applicant_name",
                "ingredients",
                "watchlist_action",
                "watchlist_id",
                "watchlist_event_id",
                "watchlist_interval_hours",
                *NOTIFICATION_FIELDS,
            )
        },
        pending_clarification_reason=state.get("clarification_question", ""),
    )




def _format_watchlist_rows(rows: list[Any]) -> str:
    if not rows:
        return "团队关注清单当前为空。"
    lines = [
        "| 关注项 ID | DMF 编号 | 状态 | 间隔（小时） | 下次检查 | 通知配置 |",
        "|---|---|---|---:|---|---|",
    ]
    for row in rows[:WATCHLIST_DISPLAY_LIMIT]:
        lines.append(
            f"| {row.id} | {row.dmf_no} | {row.status.value} | "
            f"{row.interval_hours} | {row.next_run_at or '-'} | {_notification_summary(row)} |"
        )
    if len(rows) > WATCHLIST_DISPLAY_LIMIT:
        lines.append(f"\n仅展示前 {WATCHLIST_DISPLAY_LIMIT} 项，共 {len(rows)} 项。")
    return "团队共享关注清单：\n\n" + "\n".join(lines)


def _watchlist_add(service: DMFWatchlistService, state: ResearchState) -> str:
    notification = _notification_values(state)
    if notification and notification.get("notification_enabled") is True:
        if not notification.get("notification_emails"):
            raise NotificationClarification("请提供通知接收邮箱，系统无法推断你的邮箱。")
        if not notification.get("notification_mode"):
            raise NotificationClarification("请选择有变化时通知，还是每周汇总邮件。")
    row = service.add(
        state.get("dmf_no", ""),
        state.get("watchlist_interval_hours") or 24,
        **notification,
    )
    return (
        f"已将 DMF {row.dmf_no} 加入团队共享关注清单。"
        f"检查间隔为 {row.interval_hours} 小时，关注项 ID：{row.id}。"
        + (f"{_notification_summary(row)}配置已保存，实际发送取决于后台通知服务。" if notification else "")
    )


def _watchlist_configure_notifications(service: DMFWatchlistService, state: ResearchState) -> str:
    row = service.configure_notifications(
        watchlist_id=state.get("watchlist_id", ""), dmf_no=state.get("dmf_no", ""),
        **_notification_values(state),
    )
    return (f"团队共享关注项 DMF {row.dmf_no}（ID：{row.id}）的通知配置已保存。"
            f"{_notification_summary(row)}检查间隔仍为 {row.interval_hours} 小时。"
            "实际发送取决于后台通知服务，本次操作未发送邮件。")


def _watchlist_remove(service: DMFWatchlistService, state: ResearchState) -> str:
    row = service.remove(
        watchlist_id=state.get("watchlist_id", ""),
        dmf_no=state.get("dmf_no", ""),
    )
    return f"已从团队共享关注清单删除 DMF {row.dmf_no}（关注项 ID：{row.id}）。"


def _watchlist_list(service: DMFWatchlistService, state: ResearchState) -> str:
    del state
    return _format_watchlist_rows(service.list_watchlists())


def _watchlist_run(service: DMFWatchlistService, state: ResearchState) -> tuple[str, dict[str, Any]]:
    request_id = str(state.get("request_id") or "").strip()
    if not request_id:
        raise ValueError("当前请求缺少幂等标识，请重新发送立即检查请求")
    watchlist = service.resolve(
        watchlist_id=state.get("watchlist_id", ""),
        dmf_no=state.get("dmf_no", ""),
    )
    run = service.run_manual(
        watchlist.id,
        idempotency_key=f"agent:{request_id}:{watchlist.id}",
    )
    detail = run.error_message or "检查完成"
    warnings = "；".join(run.warnings)
    warning_text = f" 注意：{warnings}" if warnings else ""
    return (
        f"DMF {watchlist.dmf_no} 的立即检查状态：{run.status.value}。{detail}{warning_text}",
        {"watchlist_id": str(watchlist.id), "dmf_no": watchlist.dmf_no, "run_status": run.status.value,
         "status": "completed" if run.status.value in {"no_change", "changed"} and not run.error_message else "failed"},
    )


def _watchlist_events(service: DMFWatchlistService, state: ResearchState) -> str:
    watchlist, events = service.list_events(
        watchlist_id=state.get("watchlist_id", ""),
        dmf_no=state.get("dmf_no", ""),
    )
    if not events:
        return f"DMF {watchlist.dmf_no} 当前暂无更新。"
    lines = [f"DMF {watchlist.dmf_no} 的团队关注事件："]
    lines.extend(
        f"- {event.id}：{event.event_type.value}，状态 {event.status.value}，"
        f"时间 {event.created_at}"
        for event in events[:WATCHLIST_DISPLAY_LIMIT]
    )
    if len(events) > WATCHLIST_DISPLAY_LIMIT:
        lines.append(f"仅展示前 {WATCHLIST_DISPLAY_LIMIT} 条，共 {len(events)} 条。")
    return "\n".join(lines)


def _watchlist_ack(service: DMFWatchlistService, state: ResearchState) -> str:
    event = service.acknowledge_event(
        state.get("watchlist_event_id", ""),
        watchlist_id=state.get("watchlist_id", ""),
        dmf_no=state.get("dmf_no", ""),
    )
    return f"已确认团队关注事件 {event.id}，状态：{event.status.value}。"


WATCHLIST_HANDLERS = {
    "add": _watchlist_add,
    "remove": _watchlist_remove,
    "list": _watchlist_list,
    "run": _watchlist_run,
    "events": _watchlist_events,
    "ack": _watchlist_ack,
    "configure_notifications": _watchlist_configure_notifications,
}


def manage_watchlist(state: ResearchState) -> dict[str, Any]:
    """Execute one validated team Watchlist action without another LLM call."""

    action = state.get("watchlist_action", "")
    notification_request = action == "configure_notifications" or bool(_notification_values(state))
    if notification_request:
        problem = _notification_request_problem(state.get("user_query") or "")
        if _is_high_impact_watchlist_request(state.get("user_query") or ""):
            problem = WATCHLIST_HIGH_IMPACT_UNSUPPORTED
        if problem:
            return ask_clarification({**state, "clarification_question": problem}) | {
                "needs_clarification": True, "clarification_question": problem,
            }
    handler = WATCHLIST_HANDLERS.get(action)
    if handler is None:
        return _answer("请说明要添加、删除、查看还是检查团队关注项。")
    status = "failed"
    business_result = {"action": action, "watchlist_id": state.get("watchlist_id", ""), "dmf_no": state.get("dmf_no", "")}
    try:
        with trace_operation(state, operation=f"watchlist.{action}"):
            answer = handler(create_watchlist_service(), state)
        status = "completed"
        if isinstance(answer, tuple):
            answer, details = answer
            business_result.update(details)
            status = details["status"]
    except WatchlistUnavailableError:
        answer = "DMF 团队关注清单暂未启用，请联系管理员。"
    except WatchlistNotFoundError:
        answer = "未找到指定的团队关注项或关注事件。"
    except WatchlistConflictError as exc:
        answer = f"团队关注清单操作未完成：{exc}。"
    except ValueError as exc:
        if notification_request:
            question = str(exc) if isinstance(exc, NotificationClarification) else (
                "请提供有效的完整收件邮箱列表；开启通知时至少需要一个邮箱，并确认关注项 ID 与 DMF 编号一致。"
            )
            return ask_clarification({**state, "clarification_question": question}) | {
                "needs_clarification": True, "clarification_question": question,
                "watchlist_result": {"action": action, "status": "needs_clarification"},
            }
        answer = f"团队关注清单参数无效：{exc}。"
    except Exception:
        logger.exception("团队关注清单操作失败，action=%s", action)
        answer = "团队关注清单操作暂时失败，请稍后重试。"
    return _answer(answer, watchlist_result={**business_result, "status": status}, pending_intent={}, pending_clarification_reason="")




def _collect_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for query_result in result.get("results", []):
        records.extend(query_result.get("records") or [])

    return records


def _format_query_condition(query: dict[str, Any]) -> str:
    conditions = [
        f"DMF 编号={query['dmf_no']}" if query.get("dmf_no") else "",
        f"申请商={query['applicant_name']}" if query.get("applicant_name") else "",
        f"成分={query['ingredient']}" if query.get("ingredient") else "",
    ]
    return "，".join(item for item in conditions if item) or "无有效条件"


def _format_empty_result_diagnostics(result: dict[str, Any]) -> str:
    query_results = result.get("results") or []
    if not query_results:
        return f"DMF 查询未完成：{result.get('message', '上游未返回查询结果')}"

    heading = (
        "没有找到符合条件的 DMF 记录。各项查询状态如下："
        if any(query_result.get("success") for query_result in query_results)
        else "DMF 查询未完成。各项查询状态如下："
    )
    lines = [heading]
    for index, query_result in enumerate(query_results, start=1):
        condition = _format_query_condition(query_result.get("query") or {})
        if query_result.get("success"):
            status = "查询成功，返回 0 条记录"
        else:
            status = f"查询失败：{query_result.get('message', '未知错误')}"
        lines.append(f"{index}. {condition}：{status}")
    history_summary = _format_history_summary(result)
    if history_summary:
        lines.extend(["", history_summary])
    return "\n".join(lines)


def _format_history_summary(result: dict[str, Any]) -> str:
    histories = [
        query_result.get("history")
        for query_result in result.get("results", [])
        if query_result.get("history")
    ]
    if not histories:
        return ""

    added = sum(history.get("added_count", 0) for history in histories)
    removed = sum(history.get("removed_count", 0) for history in histories)
    changed = sum(history.get("changed_count", 0) for history in histories)
    warnings = [
        warning
        for history in histories
        for warning in history.get("warnings", [])
    ]
    lines = [
        f"历史变化：新增 {added} 条，消失 {removed} 条，字段变化 {changed} 条。"
    ]
    lines.extend(f"注意：{warning}" for warning in dict.fromkeys(warnings))
    return "\n".join(lines)


def _format_dmf_result_text(result: dict[str, Any]) -> str:
    """确定性格式化原始 DMF 查询结果，不调用 LLM。"""

    records = _collect_records(result)

    if not records:
        return _format_empty_result_diagnostics(result)

    lines = [
        f"查询成功，共找到 **{len(records)} 条** DMF 记录。",
        "",
        "| DMF 编号 | 申请商 | 成分 | 有效日期 |",
        "|---|---|---|---|",
    ]

    for record in records:
        lines.append(
            "| "
            f"{record.get('dmf_no') or '-'} | "
            f"{record.get('applicant_name') or '-'} | "
            f"{record.get('ingredient') or '-'} | "
            f"{record.get('valid_date') or '未提供'} |"
        )

    unmatched_queries = [
        query_result
        for query_result in result.get("results", [])
        if query_result.get("success") and not query_result.get("records")
    ]
    if unmatched_queries:
        lines.extend(["", "未命中查询：", ""])
        lines.extend(
            f"{index}. "
            f"{_format_query_condition(query_result.get('query') or {})}："
            "返回 0 条记录"
            for index, query_result in enumerate(unmatched_queries, start=1)
        )

    history_summary = _format_history_summary(result)
    if history_summary:
        lines.extend(["", history_summary])

    return "\n".join(lines)
