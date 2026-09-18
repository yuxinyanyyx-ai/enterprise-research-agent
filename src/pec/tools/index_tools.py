from langchain_core.tools import tool

from src.pec.knowledge_service import (
    update_meeting_index_service,
    get_meeting_index_status_service,
)

from src.tools.registry import (
    ToolContext,
    ToolRisk,
    ToolRegistry,
    register_tool,
)


@tool(
    "get_meeting_index_status",
    description="查看PEC会议知识库当前索引状态"
)
def get_meeting_index_status() -> str:
    return get_meeting_index_status_service()


@tool(
    "update_meeting_index",
    description="更新PEC会议知识库索引，扫描新的会议资料"
)
def update_meeting_index() -> str:
    return update_meeting_index_service()



def register_index_tools(registry: ToolRegistry):

    register_tool(
        get_meeting_index_status,
        registry=registry,
        risk=ToolRisk.READ_ONLY,
        contexts={ToolContext.GENERAL},
    )

    register_tool(
        update_meeting_index,
        registry=registry,
        risk=ToolRisk.LOCAL_WRITE,
        contexts={ToolContext.GENERAL},
    )