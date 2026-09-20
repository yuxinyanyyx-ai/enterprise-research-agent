"""Agent tools for explicit, authenticated user memory management."""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import InjectedToolArg, tool

from src.agent_memory.repository import MemoryRepository
from src.tools.registry import ToolContext, ToolRegistry, ToolRisk, ToolState, register_tool


def _scope(scope: dict[str, str]) -> tuple[str, str]:
    if not isinstance(scope, dict):
        raise ValueError("缺少有效的记忆用户身份")
    user_id = str(scope.get("user_id", "")).strip()
    tenant_id = str(scope.get("tenant_id", "")).strip()
    if not user_id or not tenant_id:
        raise ValueError("缺少有效的记忆用户身份")
    return tenant_id, user_id


def _authorize_scope(state: ToolState, _call: dict[str, Any]) -> str | None:
    try:
        _scope(state.get("memory_scope", {}))
    except ValueError as exc:
        return str(exc)
    return None


def register_memory_tools(registry: ToolRegistry, repository: MemoryRepository) -> None:
    @tool(
        "remember_user_memory",
        description="保存当前认证用户明确要求记住的结构化偏好。只保存语言、称谓、输出格式等偏好，不保存凭据或完整对话。",
    )
    def remember_user_memory(
        memory_key: str,
        content: dict[str, Any],
        memory_scope: Annotated[dict[str, str], InjectedToolArg],
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        tenant_id, user_id = _scope(memory_scope)
        memory = repository.remember(
            tenant_id=tenant_id,
            user_id=user_id,
            memory_key=memory_key,
            content=content,
            idempotency_key=idempotency_key or None,
            audit_note="explicit_user_agent_tool",
        )
        return {
            "success": True,
            "memory_key": memory.memory_key,
            "content": memory.content,
            "version": memory.version,
        }

    @tool(
        "list_user_memories",
        description="列出当前认证用户已保存的长期偏好。",
    )
    def list_user_memories(
        memory_scope: Annotated[dict[str, str], InjectedToolArg],
    ) -> dict[str, Any]:
        tenant_id, user_id = _scope(memory_scope)
        memories = repository.list_active(
            tenant_id=tenant_id,
            user_id=user_id,
        )
        return {
            "success": True,
            "memories": [
                {
                    "memory_key": memory.memory_key,
                    "memory_type": memory.memory_type,
                    "content": memory.content,
                    "version": memory.version,
                }
                for memory in memories
            ],
        }

    @tool(
        "forget_user_memory",
        description="删除当前认证用户指定的长期偏好。只有用户明确要求忘记时调用。",
    )
    def forget_user_memory(
        memory_key: str,
        memory_scope: Annotated[dict[str, str], InjectedToolArg],
    ) -> dict[str, Any]:
        tenant_id, user_id = _scope(memory_scope)
        deleted = repository.forget(
            tenant_id=tenant_id,
            user_id=user_id,
            memory_key=memory_key,
        )
        return {
            "success": deleted,
            "memory_key": memory_key,
            "message": "记忆已删除。" if deleted else "未找到指定记忆。",
        }

    state_arguments = {"memory_scope": "memory_scope"}
    for memory_tool in (remember_user_memory, list_user_memories, forget_user_memory):
        register_tool(
            memory_tool,
            registry=registry,
            risk=(ToolRisk.READ_ONLY if memory_tool is list_user_memories else ToolRisk.LOCAL_WRITE),
            contexts={ToolContext.GENERAL},
            state_arguments=state_arguments,
            authorize=_authorize_scope,
            parallel_safe=False,
        )