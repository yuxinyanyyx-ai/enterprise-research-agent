from langchain_core.tools import tool

from src.tools.registry import (
    ToolContext,
    ToolRegistry,
    ToolResultStrategy,
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


def pec_search_available(state) -> bool:
    results = [
        artifact.get("result") or {}
        for artifact in state.get("tool_artifacts") or []
        if artifact.get("tool_name") == "search_pec_knowledge"
    ]
    if len(results) >= 2:
        return False
    return not any(
        isinstance(result, dict) and result.get("success") and (
            result.get("evidence") or result.get("evidence_count", 0)
            or (result.get("chunk") or {}).get("hit_count", 0)
            or (result.get("topics") or {}).get("hit_count", 0)
        )
        for result in results
    )


def register_pec_tools(registry: ToolRegistry) -> None:

    register_tool(
        search_pec_knowledge,
        registry=registry,
        risk=ToolRisk.READ_ONLY,
        contexts={ToolContext.GENERAL},
        parallel_safe=True,
        result_strategy=ToolResultStrategy.INLINE_COMPACT,
        availability=pec_search_available,
    )