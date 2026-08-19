"""Agent-facing tools for DMF business capabilities."""

from __future__ import annotations

from langchain_core.tools import tool

from src.dmf_query.multi_query_service import search_dmf_queries


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

    return search_dmf_queries(
        dmf_no=dmf_no,
        applicant_name=applicant_name,
        ingredients=ingredients,
    )
