"""
PEC PPT相关 Agent Tools
"""

from langchain_core.tools import tool


from src.pec.services.ppt_service import (
    parse_pptx_structure_service,
)


from src.tools.registry import (
    ToolContext,
    ToolRegistry,
    ToolRisk,
    register_tool,
)



@tool(
    "parse_pptx_structure",
    description=(
        "解析PPT文件结构，"
        "提取每页标题和文本内容。"
        "用于理解已有PPT内容。"
    ),
)
def parse_pptx_structure(
    file_path: str,
) -> str:

    return parse_pptx_structure_service(
        file_path
    )



def register_ppt_tools(
    registry: ToolRegistry,
):

    register_tool(
        parse_pptx_structure,
        registry=registry,
        risk=ToolRisk.READ_ONLY,
        contexts={
            ToolContext.GENERAL
        },
        parallel_safe=True,
    )