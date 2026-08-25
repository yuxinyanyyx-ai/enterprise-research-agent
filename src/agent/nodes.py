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
)
from src.services.document_dmf_service import DocumentDMFError, DocumentDMFService
from src.tools.dmf_tools import search_dmf

AGENT_SYSTEM_PROMPT = """
你是 DMF Research Agent。

你可以使用提供给你的工具完成用户请求。

规则：
1. 如果用户需要查询真实 DMF 数据，必须使用 search_dmf 工具。
2. 普通知识问题、解释性问题和日常交流，不需要调用工具。
3. 不要根据模型记忆编造 DMF 查询结果。
4. 如果已经获得工具返回的数据，应根据真实工具结果回答用户。
5. 如果还需要额外工具信息，可以继续调用工具。
"""
OUTPUT_ORDER = ("result", "summary", "analysis")
EXPORT_ACTIONS = ("导出", "下载", "生成", "保存")
EXCEL_MARKERS = ("excel", "xlsx", "exel", "表格")
CLARIFICATION_FALLBACKS = {
    "dmf_compare": (
        "请明确需要比较的成分、DMF 或申请商，"
        "以及希望比较的具体维度？"
    ),
    "document_review": (
        "请提供需要分析的文档，并说明希望重点检查哪些内容？"
    ),
    "dmf_document_compare": (
        "请提供需要核对的文档，并明确要比较的 DMF、"
        "成分或申请商？"
    ),
}
DEFAULT_CLARIFICATION = (
    "请提供需要查询的成分名称、DMF 编号或申请商名称？"
)


def _print_elapsed(name: str, start: float) -> None:
    elapsed = time.perf_counter() - start
    print(f"[耗时] {name}: {elapsed:.2f}s")


def _normalize_requested_outputs(
    task_type: str,
    requested_outputs: list[str] | None,
) -> list[str]:
    """过滤非法/重复输出，并为 DMF 查询提供安全默认值。"""

    requested = set(requested_outputs or [])
    normalized = [name for name in OUTPUT_ORDER if name in requested]

    if task_type == "dmf_query" and not normalized:
        return ["result"]

    return normalized


def _normalize_clarification_question(
    question: str | None,
    task_type: str,
) -> str:
    """Replace incomplete LLM clarification text with a stable fallback."""

    normalized = (question or "").strip()
    is_complete = (
        len(normalized) >= 10
        and normalized.endswith(("？", "?", "。", "！", "!"))
    )
    if is_complete:
        return normalized

    return CLARIFICATION_FALLBACKS.get(
        task_type,
        DEFAULT_CLARIFICATION,
    )


def _has_active_dmf_results(state: ResearchState) -> bool:
    result = state.get("dmf_results") or {}
    return bool(result.get("success") and result.get("results"))


def _requests_excel_export(user_query: str) -> bool:
    normalized = user_query.lower()
    return (
        any(action in normalized for action in EXPORT_ACTIONS)
        and any(marker in normalized for marker in EXCEL_MARKERS)
    )


def _active_dmf_context(state: ResearchState) -> dict[str, Any]:
    result = state.get("dmf_results") or {}
    return {
        "available": _has_active_dmf_results(state),
        "query": result.get("query", {}),
        "total_records": result.get("total_records", 0),
    }


def _active_document_context(state: ResearchState) -> dict[str, Any]:
    artifact = state.get("document_artifact") or {}
    return {
        "available": bool(artifact.get("markdown_path")),
        "file_name": artifact.get("file_name", ""),
        "status": artifact.get("status", ""),
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


def understand_request(state: ResearchState) -> dict[str, Any]:
    """使用 LLM 将用户自然语言转成结构化业务意图。"""

    start = time.perf_counter()
    user_query = (state.get("user_query") or "").strip()

    if not user_query:
        _print_elapsed("understand_request", start)
        return {
            "task_type": "unknown",
            "requested_outputs": [],
            "warnings": ["用户请求为空"],
        }

    request_state = _request_state(user_query)
    if _requests_excel_export(user_query):
        active_results = _has_active_dmf_results(state)
        _print_elapsed("understand_request", start)
        return {
            **request_state,
            "task_type": "dmf_post_process",
            "request_mode": "post_process",
            "requested_outputs": [],
            "needs_clarification": not active_results,
            "clarification_question": (
                ""
                if active_results
                else "当前会话没有可导出的 DMF 查询结果，请先执行查询。"
            ),
        }

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
                    f"用户最新请求：{user_query}"
                )
            ),
        ]
    )

    requested_outputs = _normalize_requested_outputs(
        intent.task_type,
        intent.requested_outputs,
    )

    _print_elapsed("understand_request", start)

    has_active_results = _has_active_dmf_results(state)
    needs_clarification = intent.needs_clarification
    clarification_question = intent.clarification_question
    if intent.task_type == "dmf_post_process":
        needs_clarification = not has_active_results
        if needs_clarification:
            clarification_question = (
                "当前会话没有可继续处理的 DMF 查询结果，请先执行查询。"
            )

    document_available = _active_document_context(state)["available"]
    if intent.task_type in {"document_review", "dmf_document_compare"}:
        needs_clarification = not document_available
        if needs_clarification:
            clarification_question = "请先使用 /file <文件路径> 上传需要分析的文档。"
        elif intent.task_type == "dmf_document_compare":
            requested_outputs = ["result"]

    if needs_clarification:
        clarification_question = _normalize_clarification_question(
            clarification_question,
            intent.task_type,
        )

    result: dict[str, Any] = {
        **request_state,
        "task_type": intent.task_type,
        "request_mode": (
            "post_process"
            if intent.task_type == "dmf_post_process"
            else "new_query"
        ),
        "requested_outputs": requested_outputs,
        "needs_clarification": needs_clarification,
        "clarification_question": clarification_question,
        "dmf_no": intent.dmf_no,
        "applicant_name": intent.applicant_name,
        "ingredients": intent.ingredients,
    }
    return result


def extract_document_query(state: ResearchState) -> dict[str, Any]:
    """Extract normalized DMF conditions from the active document."""

    try:
        query = DocumentDMFService().extract_query(state["document_artifact"])
    except (DocumentDMFError, KeyError) as exc:
        return {
            "document_status": "failed",
            "document_error": str(exc),
            "extracted_dmf_query": {},
        }
    return {
        "document_status": "extracted",
        "document_error": "",
        "extracted_dmf_query": query.model_dump(),
    }


def confirm_document_query(state: ResearchState) -> dict[str, Any]:
    """Pause before a real DMF query and accept confirmed or edited conditions."""

    candidate = ExtractedDMFQueryBatch.model_validate(state["extracted_dmf_query"])
    artifact = state.get("document_artifact") or {}
    resumed = interrupt(
        {
            "type": "document_query_confirmation",
            "message": "请确认或修改从文档中提取的 DMF 查询条件。",
            "file_name": artifact.get("file_name", ""),
            "query": candidate.model_dump(),
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
        "requested_outputs": ["result"],
        "dmf_results": result,
    }


def finalize_document_review(state: ResearchState) -> dict[str, Any]:
    """Present extracted conditions without performing a DMF query."""

    if state.get("document_error"):
        return {"final_answer": f"文档分析失败：{state['document_error']}"}
    batch = ExtractedDMFQueryBatch.model_validate(state["extracted_dmf_query"])
    lines = []
    for index, query in enumerate(batch.queries, start=1):
        ingredients = "、".join(query.ingredients) or "未提取到"
        lines.append(
            f"{index}. DMF 编号：{query.dmf_no or '未提取到'}；"
            f"申请商：{query.applicant_name or '未提取到'}；"
            f"成分：{ingredients}"
        )
    return {
        "final_answer": (
            f"已从文档中提取 {len(batch.queries)} 组 DMF 查询条件：\n"
            + "\n".join(lines)
        )
    }


def finalize_document_rejection(state: ResearchState) -> dict[str, Any]:
    return {"final_answer": "已取消使用文档条件执行 DMF 查询。"}

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

当前用户输入属于普通交流，不需要调用 DMF 查询工具。
请自然、简洁地回答用户，不要虚构不存在的系统能力。
"""
            ),
            HumanMessage(content=user_query),
        ]
    )

    return {"final_answer": str(response.content).strip()}


def ask_clarification(state: ResearchState) -> dict[str, Any]:
    question = _normalize_clarification_question(
        state.get("clarification_question"),
        state.get("task_type", "unknown"),
    )

    return {"final_answer": question}


def handle_unknown(state: ResearchState) -> dict[str, Any]:
    return {
        "final_answer": (
            "我暂时无法确定你希望执行什么任务。"
            "你可以让我查询 DMF、总结查询结果，或分析 DMF 数据。"
        )
    }


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

    lines = ["没有找到符合条件的 DMF 记录。各项查询状态如下："]
    for index, query_result in enumerate(query_results, start=1):
        condition = _format_query_condition(query_result.get("query") or {})
        if query_result.get("success"):
            status = "查询成功，返回 0 条记录"
        else:
            status = f"查询失败：{query_result.get('message', '未知错误')}"
        lines.append(f"{index}. {condition}：{status}")
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
        return {
            "final_answer": _format_empty_result_diagnostics(result)
        }

    if not records:
        _print_elapsed("build_dmf_answer", start)
        return {"final_answer": _format_empty_result_diagnostics(result)}

    requested_outputs = _normalize_requested_outputs(
        "dmf_query",
        state.get("requested_outputs"),
    )

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
    return {"final_answer": final_answer}


def finalize_dmf_post_process(state: ResearchState) -> dict[str, Any]:
    """Return only the outcome of a cross-turn DMF post-processing request."""

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
        return {"final_answer": f"DMF 查询结果已导出：\n{paths}"}

    failed = [item for item in export_results if not item.get("success")]
    if failed:
        messages = "\n".join(
            f"- {item.get('message', '导出未完成')}" for item in failed
        )
        return {"final_answer": f"DMF 导出未完成：\n{messages}"}

    if state.get("tool_stop_reason"):
        return {"final_answer": state["tool_stop_reason"]}

    messages = state.get("messages") or []
    last_message = messages[-1] if messages else None
    if isinstance(last_message, AIMessage) and last_message.content:
        return {"final_answer": str(last_message.content).strip()}
    return {"final_answer": "本次没有执行 DMF 后处理操作。"}
