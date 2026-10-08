from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool

from src.agent.artifact_store import ArtifactAccessError, ArtifactNotFound, load_artifact, published_artifact_refs
from src.agent.audit_log import write_event
from src.tools.registry import ToolContext, ToolRegistry, ToolRisk, default_result_adapter, register_tool


MAX_INLINE_ARTIFACT_BYTES = 8 * 1024


def artifact_reader_available(state) -> bool:
    return not state.get("artifact_read_disabled", False) and bool(published_artifact_refs(state))


def authorize_artifact_read(state, call) -> str | None:
    if (call.get("args") or {}).get("artifact_id") not in published_artifact_refs(state):
        return "artifact 引用不可用。请依据已有证据回答，缺失内容请明确说明。"
    return None


def apply_artifact_result(state, result) -> dict[str, Any]:
    if isinstance(result, dict) and result.get("success") is False and result.get("error_code") in {
        "unavailable", "invalid_range", "invalid_section", "invalid_reference", "duplicate_read",
    }:
        return {"artifact_read_disabled": True}
    return default_result_adapter(state, result)


def _read_failure(request_id, source_call_id, error_code, resolution, message):
    write_event(
        "artifact.read_failed", {"request_id": request_id},
        artifact_resolution=resolution, source_tool_call_id=source_call_id,
        error_code=error_code,
    )
    return {"success": False, "error_code": error_code, "message": message}


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
    published_references: Annotated[dict | None, InjectedToolArg] = None,
) -> dict[str, Any]:
    """Read a locally stored tool result within the current request."""

    reference = (published_references or {}).get(artifact_id) or {}
    source_call_id = str(reference.get("tool_call_id") or "")[:128]
    if offset < 0 or limit <= 0 or limit > 500:
        return _read_failure(request_id, source_call_id, "invalid_range", "invalid_range",
                             "offset 必须不小于 0，limit 必须在 1 到 500 之间。")

    try:
        value = load_artifact(artifact_id, request_id=request_id)
    except (OSError, ValueError) as exc:
        if isinstance(exc, ArtifactAccessError):
            resolution = "access_denied"
        elif isinstance(exc, ArtifactNotFound):
            resolution = "missing"
        elif isinstance(exc, (json.JSONDecodeError, UnicodeError)):
            resolution = "corrupt"
        elif isinstance(exc, ValueError):
            resolution = "invalid_id"
        else:
            resolution = "io_error"
        return _read_failure(request_id, source_call_id, "unavailable", resolution,
                             "artifact 不存在或不可读取。请依据已有证据回答，缺失内容请明确说明。")

    if not key:
        encoded_size = len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
        if encoded_size > MAX_INLINE_ARTIFACT_BYTES:
            sections = {}
            if isinstance(value, dict):
                for name, section in value.items():
                    if isinstance(section, list):
                        sections[name] = {"type": "list", "length": len(section)}
                    elif isinstance(section, str):
                        sections[name] = {"type": "string", "size_chars": len(section)}
                    else:
                        sections[name] = {"type": type(section).__name__}
            return {
                "success": True,
                "artifact_id": artifact_id,
                "available_sections": sections,
                "read_mode": "directory",
            }
        return {"success": True, "artifact_id": artifact_id, "value": value,
            "read_mode": "full", "context_view": "artifact_read"}
    if not isinstance(value, dict) or not isinstance(value.get(key), list):
        return _read_failure(request_id, source_call_id, "invalid_section", "invalid_section",
                     "artifact 字段不可分段读取。请依据已有证据回答。")

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
        "context_view": "artifact_read",
    }


def register_artifact_tools(registry: ToolRegistry) -> None:
    register_tool(
        read_tool_artifact,
        registry=registry,
        risk=ToolRisk.READ_ONLY,
        contexts={ToolContext.GENERAL},
        parallel_safe=True,
        state_arguments={"request_id": "request_id", "published_references": "published_artifact_refs"},
        availability=artifact_reader_available,
        authorize=authorize_artifact_read,
        result_adapter=apply_artifact_result,
    )
