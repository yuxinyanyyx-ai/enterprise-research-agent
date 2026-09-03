from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import interrupt

from src.agent.prompts import (
    DMF_ANALYSIS_SYSTEM_PROMPT,
    DMF_INTENT_SYSTEM_PROMPT,
    DMF_SUMMARY_SYSTEM_PROMPT,
)
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
from src.tools.dmf_tools import search_dmf
from src.export_excel.excel_exporter import export_multi_query_result

AGENT_SYSTEM_PROMPT = """
你是 DMF Research Agent。

你可以使用提供给你的工具完成用户请求。

规则：
1. 如果用户需要查询真实 DMF 数据，必须使用 search_dmf 工具。
2. 普通知识问题、解释性问题和日常交流，不需要调用工具。
3. 不要根据模型记忆编造 DMF 查询结果。
4. 如果已经获得工具返回的数据，应根据真实工具结果回答用户。
5. 如果还需要额外工具信息，可以继续调用工具。
6. 如果用户要求查询结果你提取不到，应明确说明情况，不要盲目分析。
7. 明确用户查询，不要乱复用上一轮的查询结果
"""
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


def _print_elapsed(name: str, start: float) -> None:
    elapsed = time.perf_counter() - start
    print(f"[耗时] {name}: {elapsed:.2f}s")


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
    }


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


def _request_state(user_query: str) -> dict[str, Any]:
    return {
        "messages": [HumanMessage(content=user_query)],
        "tool_rounds": 0,
        "max_tool_rounds": 8,
        "last_tool_batch": [],
        "tool_stop_reason": "",
        "tool_artifacts": [],
    }


def _answer(content: str, **updates: Any) -> dict[str, Any]:
    answer = content.strip()
    return {
        **updates,
        "final_answer": answer,
        "messages": [AIMessage(content=answer)],
    }


def _recent_conversation(state: ResearchState, limit: int = 12) -> list[dict[str, str]]:
    conversation = []
    for message in state.get("messages") or []:
        if not isinstance(message, (HumanMessage, AIMessage)):
            continue
        role = "human" if isinstance(message, HumanMessage) else "ai"
        conversation.append({"role": role, "content": str(message.content)})
    return conversation[-limit:]


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
        "pending_intent": {},
        "pending_clarification_reason": "",
    }

    if intent.needs_clarification:
        return {
            **base,
            "needs_clarification": True,
            "clarification_question": AMBIGUOUS_REQUEST_CLARIFICATION,
        }

    if intent.data_source == "none":
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


def understand_request(state: ResearchState) -> dict[str, Any]:
    """使用 LLM 将用户自然语言转成结构化业务意图。"""

    start = time.perf_counter()
    user_query = (state.get("user_query") or "").strip()

    if not user_query:
        _print_elapsed("understand_request", start)
        return {
            **_request_state(user_query),
            "data_source": "none",
            "use_existing_data": False,
            "query_document_conditions": False,
            "requested_outputs": [],
            "needs_clarification": True,
            "clarification_question": AMBIGUOUS_REQUEST_CLARIFICATION,
            "warnings": ["用户请求为空"],
        }

    request_state = _request_state(user_query)
    llm = create_apollo_llm()
    structured_llm = llm.with_structured_output(ResearchIntent)

    intent = structured_llm.invoke(
        [
            SystemMessage(content=DMF_INTENT_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "当前 DMF 结果上下文：\n"
                    f"{json.dumps(_active_dmf_context(state), ensure_ascii=False)}\n\n"
                    "当前上传文档上下文：\n"
                    f"{json.dumps(_active_document_context(state), ensure_ascii=False)}\n\n"
                    "当前已提取文档条件上下文：\n"
                    f"{json.dumps(_active_extracted_query_context(state), ensure_ascii=False)}\n\n"
                    "待补全意图：\n"
                    f"{json.dumps(state.get('pending_intent') or {}, ensure_ascii=False)}\n\n"
                    "最近 6 轮完整对话：\n"
                    f"{json.dumps(_recent_conversation(state), ensure_ascii=False)}\n\n"
                    f"用户最新请求：{user_query}"
                )
            ),
        ]
    )

    _print_elapsed("understand_request", start)
    return {**request_state, **_validate_intent(intent, state)}


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
    return {
        "document_query_decision": decision.action,
        "confirmed_dmf_query": (
            decision.query.model_dump() if decision.query is not None else {}
        ),
    }


def query_confirmed_document_dmf(state: ResearchState) -> dict[str, Any]:
    """Run a DMF query only after document conditions were confirmed."""

    query = ExtractedDMFQueryBatch.model_validate(state["confirmed_dmf_query"])
    result = DocumentDMFService().execute_confirmed_query(query)
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

def general_chat(state: ResearchState) -> dict[str, Any]:
    """处理不需要业务工具的普通交流。"""

    user_query = (state.get("user_query") or "").strip()
    llm = create_apollo_llm()

    response = llm.invoke(
        [
            SystemMessage(
                content="""
你是 DMF Research Agent。

你可以帮助用户：
- 查询 DMF 信息
- 查询成分、申请商或 DMF 编号
- 对查询结果进行总结
- 对 DMF 数据进行分析
- 上传并解析 PDF、Word、图片、Excel、Markdown 或文本文件
- 从当前文档提取 DMF 查询条件并在确认后查询

当前用户输入属于普通交流，不需要调用 DMF 查询工具。
系统会提供当前会话的可信文档清单。涉及当前文档时只能依据该清单回答。
请自然、简洁地回答用户，不要否认上述真实能力，也不要虚构其他能力。
"""
            ),
            HumanMessage(
                content=(
                    "当前可信文档上下文：\n"
                    f"{json.dumps(_active_document_context(state), ensure_ascii=False)}\n\n"
                    "近期完整对话：\n"
                    f"{json.dumps(_recent_conversation(state), ensure_ascii=False)}\n\n"
                    f"用户最新请求：{user_query}"
                )
            ),
        ]
    )

    return _answer(str(response.content))


def ask_clarification(state: ResearchState) -> dict[str, Any]:
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
            )
        },
        pending_clarification_reason=state.get("clarification_question", ""),
    )


def handle_unknown(state: ResearchState) -> dict[str, Any]:
    return _answer(
        "我暂时无法确定你希望执行什么任务。"
        "你可以让我查询 DMF、总结查询结果，或分析 DMF 数据。"
    )


def query_dmf(state: ResearchState) -> dict[str, Any]:
    """根据 Workflow State 中的查询条件调用 DMF Tool。"""

    start = time.perf_counter()

    result = search_dmf.invoke(
        {
            "dmf_no": state.get("dmf_no", ""),
            "applicant_name": state.get("applicant_name", ""),
            "ingredients": state.get("ingredients", []),
        }
    )

    _print_elapsed("query_dmf", start)
    return {"dmf_results": result}


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


def _summarize_dmf_text(result: dict[str, Any]) -> str:
    """基于真实查询结果生成摘要文本。"""

    llm = create_apollo_llm()
    response = llm.invoke(
        [
            SystemMessage(content=DMF_SUMMARY_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "下面是系统真实查询得到的 DMF 数据。\n"
                    "请进行简要摘要，不要进行深入分析。\n\n"
                    f"{json.dumps(result, ensure_ascii=False, indent=2)}"
                )
            ),
        ]
    )

    return str(response.content).strip()


def _build_analysis_result(result: dict[str, Any]) -> dict[str, Any]:
    """生成供 LLM 使用的确定性统计信息。"""

    return {
        "success": True,
        "total_records": result.get("total_records", 0),
        "query_count": result.get("query_count", 0),
        "success_count": result.get("success_count", 0),
        "failed_count": result.get("failed_count", 0),
    }


def _analyze_dmf_text(
    state: ResearchState,
    result: dict[str, Any],
) -> str:
    """基于真实查询结果生成用户明确要求的分析文本。"""

    context = {
        "user_query": (state.get("user_query") or "").strip(),
        "analysis_result": _build_analysis_result(result),
        "dmf_results": result,
    }

    llm = create_apollo_llm()
    response = llm.invoke(
        [
            SystemMessage(content=DMF_ANALYSIS_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "下面是用户问题、系统真实查询数据和确定性统计信息。\n"
                    "请只生成分析部分，不要重复完整查询结果表。\n\n"
                    f"{json.dumps(context, ensure_ascii=False, indent=2)}"
                )
            ),
        ]
    )

    return str(response.content).strip()


def build_dmf_answer(state: ResearchState) -> dict[str, Any]:
    """按照 requested_outputs 组合用户最终需要的一个或多个输出。"""

    start = time.perf_counter()
    result = state.get("dmf_results") or {}

    records = _collect_records(result)
    if not result.get("success") and not records:
        _print_elapsed("build_dmf_answer", start)
        return _answer(_format_empty_result_diagnostics(result))

    if not records:
        _print_elapsed("build_dmf_answer", start)
        return _answer(_format_empty_result_diagnostics(result))

    requested_outputs = [
        output for output in _normalize_requested_outputs(
        True,
        state.get("requested_outputs"),
        ) if output in OUTPUT_ORDER
    ]

    output_text: dict[str, str] = {}

    if "result" in requested_outputs:
        output_text["result"] = _format_dmf_result_text(result)

    if "summary" in requested_outputs:
        output_text["summary"] = _summarize_dmf_text(result)

    if "analysis" in requested_outputs:
        output_text["analysis"] = _analyze_dmf_text(state, result)

    # 单一输出时不额外加标题；组合输出时加标题，避免内容混在一起。
    if len(requested_outputs) == 1:
        final_answer = output_text[requested_outputs[0]]
    else:
        titles = {
            "result": "查询结果",
            "summary": "摘要",
            "analysis": "分析",
        }
        parts = [
            f"## {titles[name]}\n{output_text[name]}"
            for name in requested_outputs
        ]
        final_answer = "\n\n".join(parts)

    if result.get("failed_count", 0):
        final_answer = (
            f"{final_answer}\n\n> 部分查询未完成："
            f"成功 {result.get('success_count', 0)}，"
            f"失败 {result.get('failed_count', 0)}。"
        )

    export_results = [
        artifact.get("result", {})
        for artifact in state.get("tool_artifacts", [])
        if artifact.get("tool_name") == "export_dmf_excel"
    ]
    successful_exports = [
        item for item in export_results if item.get("success")
    ]
    failed_exports = [
        item for item in export_results if not item.get("success")
    ]
    if successful_exports:
        export_lines = [
            f"- `{item.get('file_path', '')}`"
            for item in successful_exports
        ]
        final_answer = (
            f"{final_answer}\n\n## 导出文件\n"
            + "\n".join(export_lines)
        )
    if failed_exports:
        failure_lines = [
            f"- {item.get('message', '导出未完成')}"
            for item in failed_exports
        ]
        final_answer = (
            f"{final_answer}\n\n## 导出状态\n"
            + "\n".join(failure_lines)
        )

    if state.get("tool_stop_reason"):
        final_answer = (
            f"{final_answer}\n\n> 工具执行已停止："
            f"{state['tool_stop_reason']}"
        )

    _print_elapsed("build_dmf_answer", start)
    return _answer(final_answer)


def finalize_dmf_export(state: ResearchState) -> dict[str, Any]:
    """Return only the outcome of a cross-turn DMF export request."""

    export_results = [
        artifact.get("result", {})
        for artifact in state.get("tool_artifacts", [])
        if artifact.get("tool_name") == "export_dmf_excel"
    ]
    successful = [item for item in export_results if item.get("success")]
    if successful:
        paths = "\n".join(
            f"- `{item.get('file_path', '')}`" for item in successful
        )
        return _answer(f"DMF 查询结果已导出：\n{paths}")

    failed = [item for item in export_results if not item.get("success")]
    if failed:
        messages = "\n".join(
            f"- {item.get('message', '导出未完成')}" for item in failed
        )
        return _answer(f"DMF 导出未完成：\n{messages}")

    if state.get("tool_stop_reason"):
        return _answer(state["tool_stop_reason"])

    messages = state.get("messages") or []
    last_message = messages[-1] if messages else None
    if isinstance(last_message, AIMessage) and last_message.content:
        return {"final_answer": str(last_message.content).strip()}
    return _answer("本次没有执行 DMF 后处理操作。")


def export_dmf_results(state: ResearchState) -> dict[str, Any]:
    """Export the active trusted DMF result without another LLM decision."""

    try:
        output_path = export_multi_query_result(state.get("dmf_results") or {})
        result = {
            "success": True,
            "message": "DMF 查询结果已导出为 Excel。",
            "file_path": str(output_path),
            "file_name": output_path.name,
        }
    except Exception as exc:
        result = {
            "success": False,
            "message": f"导出失败：{exc}",
        }
    return {
        "tool_artifacts": [
            {
                "tool_name": "export_dmf_excel",
                "tool_call_id": "deterministic-export",
                "result": result,
            }
        ]
    }
