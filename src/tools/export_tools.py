"""Agent tools for exporting deterministic DMF query results."""

from __future__ import annotations

import re
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool

from src.export_excel.excel_exporter import export_multi_query_result
from src.tools.registry import ToolCall, ToolContext, ToolRegistry, ToolRisk, ToolState, register_tool
from src.schemas.dmf import DMFSearchResult


@tool(
    "export_dmf_excel",
    description=(
        "将本次已经完成的 DMF 查询结果导出为 Excel 文件。"
        "仅当用户明确要求导出或下载 Excel 时调用。"
    ),
)
def export_dmf_excel(
    dmf_results: Annotated[dict[str, Any], InjectedToolArg],
    filename: str = "",
) -> dict[str, Any]:
    """Export trusted DMF state; dmf_results is injected by the executor."""

    output_path = export_multi_query_result(
        DMFSearchResult.model_validate(dmf_results).model_dump(mode="json"),
        filename=filename or None,
    )
    return {
        "success": True,
        "message": "DMF 查询结果已导出为 Excel。",
        "file_path": str(output_path),
        "file_name": output_path.name,
    }


def export_available(state: ToolState) -> bool:
    result = state.get("dmf_results") or {}
    return bool(state.get("result_id") and isinstance(result.get("results"), list) and result.get("results"))


def authorize_export(state: ToolState, call: ToolCall) -> str | None:
    if not re.search(r"导出|下载|export|download|excel|exel", state.get("user_query", ""), re.IGNORECASE):
        return "只有用户明确要求导出时才能生成 Excel。"
    return None


def register_export_tools(registry: ToolRegistry) -> None:
    register_tool(
        export_dmf_excel,
        registry=registry,
        risk=ToolRisk.LOCAL_WRITE,
        contexts={ToolContext.GENERAL, ToolContext.DMF_EXPORT},
        parallel_safe=False,
        state_arguments={"dmf_results": "dmf_results"},
        availability=export_available,
        authorize=authorize_export,
        reuse_result=True,
        deduplication_state=("result_id",),
    )