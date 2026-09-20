"""Central registry for Agent-callable tools and execution policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Any, Callable, Mapping

from langchain_core.tools import BaseTool


class ToolRisk(StrEnum):
    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    EXTERNAL_WRITE = "external_write"


class ToolKind(StrEnum):
    FUNCTION = "function"
    WORKFLOW_HANDOFF = "workflow_handoff"


class ToolContext(StrEnum):
    GENERAL = "general"
    DMF_EXPORT = "dmf_export"


ToolState = Mapping[str, Any]
ToolCall = Mapping[str, Any]


def always_available(state: ToolState) -> bool:
    return True


def default_result_adapter(state: ToolState, result: Any) -> dict[str, Any]:
    if isinstance(result, dict) and result.get("success") is False:
        return {"react_tool_stop_reason": result.get("message") or "工具执行失败。"}
    return {}


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    tool: BaseTool
    risk: ToolRisk
    contexts: frozenset[ToolContext]
    kind: ToolKind = ToolKind.FUNCTION
    parallel_safe: bool = False
    state_arguments: tuple[tuple[str, str], ...] = ()
    availability: Callable[[ToolState], bool] = always_available
    authorize: Callable[[ToolState, ToolCall], str | None] | None = None
    result_adapter: Callable[[ToolState, Any], dict[str, Any]] = default_result_adapter
    reuse_result: bool = False
    deduplication_state: tuple[str, ...] = ()
    repeat_message: str = "本请求已执行相同工具调用，请使用已有结果。"

    def execution_problem(self, state: ToolState, call: ToolCall) -> str | None:
        if not self.availability(state):
            return "当前状态不允许使用该工具。"
        if self.authorize is not None:
            return self.authorize(state, call)
        if self.risk is not ToolRisk.READ_ONLY:
            return "写操作缺少明确的授权策略。"
        return None


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

    def clone(self) -> ToolRegistry:
        cloned = ToolRegistry()
        for definition in self._definitions.values():
            cloned.register(definition)
        return cloned


ToolProvider = Callable[[ToolRegistry], None]
_builtin_registry: ToolRegistry | None = None
_builtin_lock = Lock()


def register_tool(
    tool: BaseTool,
    *,
    registry: ToolRegistry,
    risk: ToolRisk,
    contexts: set[ToolContext],
    kind: ToolKind = ToolKind.FUNCTION,
    parallel_safe: bool = False,
    state_arguments: dict[str, str] | None = None,
    availability: Callable[[ToolState], bool] = always_available,
    authorize: Callable[[ToolState, ToolCall], str | None] | None = None,
    result_adapter: Callable[[ToolState, Any], dict[str, Any]] = default_result_adapter,
    reuse_result: bool = False,
    deduplication_state: tuple[str, ...] = (),
    repeat_message: str = "本请求已执行相同工具调用，请使用已有结果。",
) -> BaseTool:
    registry.register(
        ToolDefinition(
            tool=tool,
            risk=risk,
            contexts=frozenset(contexts),
            kind=kind,
            parallel_safe=parallel_safe,
            state_arguments=tuple((state_arguments or {}).items()),
            availability=availability,
            authorize=authorize,
            result_adapter=result_adapter,
            reuse_result=reuse_result,
            deduplication_state=deduplication_state,
            repeat_message=repeat_message,
        )
    )
    return tool


def build_builtin_registry(providers: tuple[ToolProvider, ...] | None = None) -> ToolRegistry:
    if providers is None:
        from src.tools.providers import BUILTIN_PROVIDERS

        providers = BUILTIN_PROVIDERS
    registry = ToolRegistry()
    for provider in providers:
        provider(registry)
    return registry


def load_builtin_tools() -> ToolRegistry:
    """Build and cache the production registry once, without import side effects."""
    global _builtin_registry
    with _builtin_lock:
        if _builtin_registry is None:
            _builtin_registry = build_builtin_registry()
        return _builtin_registry