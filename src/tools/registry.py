"""Central registry for Agent-callable tools and execution policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from langchain_core.tools import BaseTool


class ToolRisk(StrEnum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    EXTERNAL_WRITE = "external_write"


class ToolContext(StrEnum):
    GENERAL = "general"
    DMF_POST_PROCESS = "dmf_post_process"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    tool: BaseTool
    risk: ToolRisk
    contexts: frozenset[ToolContext]
    parallel_safe: bool = False
    state_arguments: tuple[tuple[str, str], ...] = ()

    @property
    def requires_approval(self) -> bool:
        return self.risk is not ToolRisk.READ_ONLY


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        name = definition.tool.name
        if name in self._definitions:
            raise ValueError(f"工具名称重复：{name}")
        self._definitions[name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise KeyError(f"未注册的工具：{name}") from exc

    def for_context(self, context: ToolContext) -> list[ToolDefinition]:
        return [
            definition
            for definition in self._definitions.values()
            if context in definition.contexts
        ]


tool_registry = ToolRegistry()


def register_tool(
    tool: BaseTool,
    *,
    risk: ToolRisk,
    contexts: set[ToolContext],
    parallel_safe: bool = False,
    state_arguments: dict[str, str] | None = None,
) -> BaseTool:
    tool_registry.register(
        ToolDefinition(
            tool=tool,
            risk=risk,
            contexts=frozenset(contexts),
            parallel_safe=parallel_safe,
            state_arguments=tuple((state_arguments or {}).items()),
        )
    )
    return tool


def load_builtin_tools() -> ToolRegistry:
    """Import built-in tools once so their explicit registrations run."""

    from src.tools import dmf_tools, export_tools, workflow_tools  # noqa: F401

    return tool_registry