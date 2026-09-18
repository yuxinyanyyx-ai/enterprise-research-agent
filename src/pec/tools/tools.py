from langchain_core.tools import tool

from src.tools.registry import (
    ToolContext,
    ToolRegistry,
    ToolRisk,
    register_tool,
)


@tool(
    "search_pec_knowledge",
    description=(
        "查询 PEC 会议知识库。"
        "用于检索历史会议资料、讨论内容、决策和行动项。"
    ),
)
def search_pec_knowledge(
    query: str,
) -> dict:
    from src.pec.tools import search_pec_knowledge_service

    return search_pec_knowledge_service(query)


def register_pec_tools(registry: ToolRegistry) -> None:

    register_tool(
        search_pec_knowledge,
        registry=registry,
        risk=ToolRisk.READ_ONLY,
        contexts={ToolContext.GENERAL},
        parallel_safe=True,
    )