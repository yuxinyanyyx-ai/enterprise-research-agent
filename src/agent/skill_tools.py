from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from src.agent.skills import SkillCatalogError, load_skill_catalog
from src.tools.registry import (
    ToolContext,
    ToolRegistry,
    ToolRisk,
    register_tool,
)


@tool(
    "load_skill",
    description=(
        "加载 catalog 中列出的 Skill 指引。仅在当前用户请求与该 Skill 相关、"
        "且相应指引尚未加载时调用。skill_id 必须使用 catalog 中的精确 id。"
    ),
)
def load_skill(skill_id: str) -> dict[str, Any]:
    skill = load_skill_catalog().get(skill_id)
    return {"success": True, "skill_id": skill.id}


def skill_load_available(state) -> bool:
    active = set(state.get("active_skill_ids") or [])
    return any(skill.id not in active for skill in load_skill_catalog().skills)


def authorize_skill_load(state, call) -> str | None:
    skill_id = str((call.get("args") or {}).get("skill_id", ""))
    try:
        load_skill_catalog().get(skill_id)
    except SkillCatalogError as exc:
        return str(exc)
    if skill_id in set(state.get("active_skill_ids") or []):
        return f"Skill {skill_id} 已在本轮加载，无需重复加载。"
    return None


def activate_loaded_skill(state, result) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("success") is not True:
        return {}
    skill_id = result.get("skill_id")
    if not isinstance(skill_id, str):
        return {}
    catalog = load_skill_catalog()
    catalog.get(skill_id)
    active = list(dict.fromkeys([*(state.get("active_skill_ids") or []), skill_id]))
    return {"active_skill_ids": active}


def register_skill_tools(registry: ToolRegistry) -> None:
    load_skill_catalog().validate_tools({
        item.tool.name for item in registry.for_context(ToolContext.GENERAL)
    })
    register_tool(
        load_skill,
        registry=registry,
        risk=ToolRisk.READ_ONLY,
        contexts={ToolContext.GENERAL},
        availability=skill_load_available,
        authorize=authorize_skill_load,
        result_adapter=activate_loaded_skill,
    )
