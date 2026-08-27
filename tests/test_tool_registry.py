import pytest
from langchain_core.tools import tool

from src.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolRisk,
    load_builtin_tools,
)


def test_builtin_tools_are_filtered_by_context() -> None:
    registry = load_builtin_tools()

    general_names = {
        item.tool.name for item in registry.for_context(ToolContext.GENERAL)
    }
    post_process_names = {
        item.tool.name
        for item in registry.for_context(ToolContext.DMF_EXPORT)
    }

    assert general_names == set()
    assert post_process_names == {"export_dmf_excel"}
    assert registry.get("export_dmf_excel").risk is ToolRisk.LOCAL_WRITE
    assert "dmf_results" not in registry.get("export_dmf_excel").tool.args
    assert registry.get("export_dmf_excel").state_arguments == (
        ("dmf_results", "dmf_results"),
    )


def test_registry_rejects_duplicate_names() -> None:
    @tool("duplicate")
    def first_tool() -> str:
        """First test tool."""
        return "first"

    registry = ToolRegistry()
    definition = ToolDefinition(
        tool=first_tool,
        risk=ToolRisk.READ_ONLY,
        contexts=frozenset({ToolContext.GENERAL}),
    )
    registry.register(definition)

    with pytest.raises(ValueError, match="工具名称重复"):
        registry.register(definition)


def test_registry_rejects_unknown_tool() -> None:
    with pytest.raises(KeyError, match="未注册的工具"):
        ToolRegistry().get("missing")