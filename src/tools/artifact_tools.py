from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool

from src.agent.artifact_store import ArtifactAccessError, ArtifactNotFound, load_artifact
from src.tools.registry import ToolContext, ToolRegistry, ToolRisk, register_tool


@tool(
    "read_tool_artifact",
    description=(
        "读取之前工具调用保存的完整结果。大结果优先按顶层列表字段分段读取；"
        "artifact_id 来自工具返回的 artifact_ref。"
    ),
)
def read_tool_artifact(
    artifact_id: str,
    key: str = "",
    offset: int = 0,
    limit: int = 50,
    request_id: Annotated[str, InjectedToolArg] = "",
) -> dict[str, Any]:
    """Read a locally stored tool result within the current request."""

    if offset < 0 or limit <= 0 or limit > 500:
        return {"success": False, "message": "offset 必须不小于 0，limit 必须在 1 到 500 之间。"}

    try:
        value = load_artifact(artifact_id, request_id=request_id)
    except (ArtifactAccessError, ArtifactNotFound, ValueError):
        return {"success": False, "message": "artifact 不存在或不可读取。"}

    if not key:
        return {"success": True, "artifact_id": artifact_id, "value": value}
    if not isinstance(value, dict) or not isinstance(value.get(key), list):
        return {"success": False, "message": f"artifact 字段不可分段读取：{key}"}

    values = value[key]
    return {
        "success": True,
        "artifact_id": artifact_id,
        "key": key,
        "offset": offset,
        "limit": limit,
        "total": len(values),
        "items": values[offset:offset + limit],
        "next_offset": offset + limit if offset + limit < len(values) else None,
    }


def register_artifact_tools(registry: ToolRegistry) -> None:
    register_tool(
        read_tool_artifact,
        registry=registry,
        risk=ToolRisk.READ_ONLY,
        contexts={ToolContext.GENERAL},
        parallel_safe=True,
        state_arguments={"request_id": "request_id"},
    )
