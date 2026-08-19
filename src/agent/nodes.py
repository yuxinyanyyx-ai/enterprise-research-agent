from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.prompts import (
    DMF_ANALYSIS_SYSTEM_PROMPT,
    DMF_INTENT_SYSTEM_PROMPT,
    DMF_SUMMARY_SYSTEM_PROMPT,
)
from src.agent.state import ResearchState
from src.llm.apollo import create_apollo_llm
from src.schemas.intent import ResearchIntent
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

    llm = create_apollo_llm()
    structured_llm = llm.with_structured_output(ResearchIntent)

    intent = structured_llm.invoke(
        [
            SystemMessage(content=DMF_INTENT_SYSTEM_PROMPT),
            HumanMessage(content=user_query),
        ]
    )

    requested_outputs = _normalize_requested_outputs(
        intent.task_type,
        intent.requested_outputs,
    )

    _print_elapsed("understand_request", start)

    return {
        "task_type": intent.task_type,
        "requested_outputs": requested_outputs,
        "needs_clarification": intent.needs_clarification,
        "clarification_question": intent.clarification_question,
        "dmf_no": intent.dmf_no,
        "applicant_name": intent.applicant_name,
        "ingredients": intent.ingredients,
    }
def agent_node(
    state: ResearchState,
) -> dict[str, Any]:

    user_query = (
        state.get("user_query") or ""
    ).strip()

    messages = list(
        state.get("messages") or []
    )

    llm = create_apollo_llm()

    llm_with_tools = llm.bind_tools(
        [search_dmf]
    )

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
    question = state.get("clarification_question")

    if not question:
        question = "请提供需要查询的成分名称、DMF 编号或申请商名称。"

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


def _format_dmf_result_text(result: dict[str, Any]) -> str:
    """确定性格式化原始 DMF 查询结果，不调用 LLM。"""

    records = _collect_records(result)

    if not records:
        return "查询成功，但没有找到符合条件的 DMF 记录。"

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

    if not result.get("success"):
        _print_elapsed("build_dmf_answer", start)
        return {
            "final_answer": (
                f"DMF 查询失败：{result.get('message', '未知错误')}"
            )
        }

    records = _collect_records(result)
    if not records:
        _print_elapsed("build_dmf_answer", start)
        return {"final_answer": "查询成功，但没有找到符合条件的 DMF 记录。"}

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

    _print_elapsed("build_dmf_answer", start)
    return {"final_answer": final_answer}
