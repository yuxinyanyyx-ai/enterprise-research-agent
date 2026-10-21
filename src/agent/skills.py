from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / "skills"
CATALOG_PATH = SKILLS_ROOT / "catalog.json"
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
EXPLICIT_SKILL_RE = re.compile(r"\$([a-zA-Z0-9][\w-]*)")


class SkillCatalogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Skill:
    id: str
    name: str
    description: str
    path: Path
    instructions: str
    triggers: tuple[str, ...]
    tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillCatalog:
    skills: tuple[Skill, ...]

    def by_id(self) -> dict[str, Skill]:
        return {skill.id: skill for skill in self.skills}

    def get(self, skill_id: str) -> Skill:
        try:
            return self.by_id()[skill_id]
        except KeyError as exc:
            raise SkillCatalogError(f"未知的 Skill：{skill_id}") from exc

    def select_for_message(self, message: str) -> tuple[str, ...]:
        text = message or ""
        by_id = self.by_id()
        explicit = tuple(dict.fromkeys(
            match.group(1) for match in EXPLICIT_SKILL_RE.finditer(text)
            if match.group(1) in by_id
        ))

        lowered = text.casefold()
        triggered = tuple(
            skill.id
            for skill in self.skills
            if skill.id not in explicit
            and any(trigger.casefold() in lowered for trigger in skill.triggers)
        )
        return (*explicit, *triggered)

    def catalog_block(self, *, loader_available: bool = True) -> str:
        lines = [
            "# Available skills",
            (
                "Choose a matching skill. If its instructions are not active, call "
                "`load_skill` with its exact id before following its workflow."
                if loader_available else
                "Choose from available skills; follow only instructions present in Active skills."
            ),
        ]
        lines.extend(
            f"- `{skill.id}`: {skill.description}"
            for skill in self.skills
        )
        return "\n".join(lines)

    def render_instructions(self, skill_ids: list[str] | tuple[str, ...]) -> str:
        selected = tuple(dict.fromkeys(skill_ids))
        if not selected:
            return ""
        parts = ["# Active skills", "Follow these trusted repository instructions for this request."]
        for skill_id in selected:
            skill = self.get(skill_id)
            parts.append(f"## Skill: {skill.id}\n\n{skill.instructions}")
        return "\n\n".join(parts)

    def validate_tools(self, registered_tools: set[str]) -> None:
        missing = sorted({
            tool_name
            for skill in self.skills
            for tool_name in skill.tools
            if tool_name not in registered_tools
        })
        if missing:
            raise SkillCatalogError(
                "Skill 声明了未注册的 Agent 工具：" + ", ".join(missing)
            )


def _as_tuple(value: Any, *, field: str, skill_id: str) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    values = value if isinstance(value, list) else re.split(r"[,|，]", str(value))
    result = tuple(str(item).strip() for item in values if str(item).strip())
    if any(not item for item in result):
        raise SkillCatalogError(f"Skill {skill_id} 的 {field} 元数据无效")
    return result


def _load_skill(entry: dict[str, Any]) -> Skill:
    skill_id = str(entry.get("id", "")).strip()
    relative_path = str(entry.get("path", "")).strip()
    if not skill_id or not relative_path:
        raise SkillCatalogError("catalog.json 中每项都必须有 id 和 path")

    path = (SKILLS_ROOT / relative_path).resolve()
    if not path.is_relative_to(SKILLS_ROOT.resolve()):
        raise SkillCatalogError(f"Skill 路径越界：{skill_id}")
    if not path.is_file():
        raise SkillCatalogError(f"Skill 文件不存在：{relative_path}")

    raw = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(raw.strip())
    if not match:
        raise SkillCatalogError(f"Skill 缺少有效的 YAML frontmatter：{relative_path}")
    metadata = yaml.safe_load(match.group(1))
    if not isinstance(metadata, dict):
        raise SkillCatalogError(f"Skill frontmatter 必须是键值结构：{relative_path}")

    tool_metadata = metadata.get("tools", metadata.get("allowed-tools", ()))
    return Skill(
        id=skill_id,
        name=str(entry.get("name") or metadata.get("name") or skill_id),
        description=str(entry.get("description") or metadata.get("description") or ""),
        path=path,
        instructions=match.group(2).strip(),
        triggers=_as_tuple(metadata.get("triggers"), field="triggers", skill_id=skill_id),
        tools=_as_tuple(tool_metadata, field="tools", skill_id=skill_id),
    )


@lru_cache(maxsize=1)
def load_skill_catalog() -> SkillCatalog:
    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillCatalogError(f"无法读取 Skill catalog：{exc}") from exc
    entries = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise SkillCatalogError("catalog.json 必须包含 skills 数组")

    skills = tuple(_load_skill(entry) for entry in entries)
    ids = [skill.id for skill in skills]
    if len(ids) != len(set(ids)):
        raise SkillCatalogError("catalog.json 中存在重复 Skill id")
    return SkillCatalog(skills=skills)


def build_system_prompt(
    skill_ids: list[str] | tuple[str, ...] = (),
    *,
    loader_available: bool = True,
) -> str:
    from src.agent.prompts import BASE_REACT_SYSTEM_PROMPT

    catalog = load_skill_catalog()
    parts = [BASE_REACT_SYSTEM_PROMPT, catalog.catalog_block(loader_available=loader_available)]
    active = catalog.render_instructions(skill_ids)
    if active:
        parts.append(active)
    return "\n\n".join(parts)
