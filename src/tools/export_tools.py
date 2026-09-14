"""Agent tools for exporting deterministic DMF query results."""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool

from src.export_excel.excel_exporter import export_multi_query_result
from src.tools.registry import ToolContext, ToolRisk, register_tool
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


register_tool(
    export_dmf_excel,
    risk=ToolRisk.LOCAL_WRITE,
    contexts={ToolContext.GENERAL, ToolContext.DMF_EXPORT},
    parallel_safe=False,
    state_arguments={"dmf_results": "dmf_results"},
)