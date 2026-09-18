"""Agent-facing tools for DMF business capabilities."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from langchain_core.tools import tool

from src.dmf_query.multi_query_service import search_dmf_queries
from src.tools.registry import ToolContext, ToolRegistry, ToolRisk, ToolState, register_tool


@tool(
    "search_dmf",
    description=(
        "查询 DMF（Drug Master File）数据。"
        "支持按 DMF 编号、申请商名称、一个或多个成分查询。"
        "工具会自动处理验证码、MinerU 识别和分页查询，"
        "并返回完整结构化 DMF 记录。"
    ),
)
def search_dmf(
    dmf_no: str = "",
    applicant_name: str = "",
    ingredients: list[str] | None = None,
) -> dict:
    """Thin Agent adapter over the DMF business service."""

    dmf_no = dmf_no.strip()
    applicant_name = applicant_name.strip()
    ingredients = [value.strip() for value in ingredients or [] if value.strip()]
    if not (dmf_no or applicant_name or ingredients):
        raise ValueError("请提供至少一个有效的 DMF 编号、申请商或成分查询条件。")
    return search_dmf_queries(
        dmf_no=dmf_no,
        applicant_name=applicant_name,
        ingredients=ingredients,
    )


def apply_dmf_result(state: ToolState, result: Any) -> dict[str, Any]:
    valid = isinstance(result, dict) and isinstance(result.get("results"), list)
    failed = isinstance(result, dict) and result.get("success") is False and not (
        result.get("success_count", 0) or any(
            item.get("success") or item.get("records")
            for item in result.get("results", []) if isinstance(item, dict)
        )
    )
    updates = {
        "dmf_results": result if valid else {},
        "result_id": uuid4().hex if valid and not failed else "",
        "result_source": "query",
        "domain_pending": {},
    }
    if failed:
        updates["react_tool_stop_reason"] = result.get("message") or "查询失败。"
    return updates


def register_dmf_tools(registry: ToolRegistry) -> None:
    register_tool(
        search_dmf,
        registry=registry,
        risk=ToolRisk.READ_ONLY,
        contexts={ToolContext.GENERAL},
        parallel_safe=True,
        result_adapter=apply_dmf_result,
        repeat_message="本请求已执行相同查询；多次独立查询不会自动合并，请明确当前需要的结果。",
    )
