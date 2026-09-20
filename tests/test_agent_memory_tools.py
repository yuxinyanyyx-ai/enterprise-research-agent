import json

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from src.agent import tooling
from src.agent_memory.repository import MemoryRepository
from src.tools.memory_tools import register_memory_tools
from src.tools.registry import ToolContext, ToolRegistry


def _memory_registry(tmp_path) -> ToolRegistry:
    repository = MemoryRepository(f"sqlite:///{tmp_path / 'memory.db'}")
    repository.initialize_schema()
    registry = ToolRegistry()
    register_memory_tools(registry, repository)
    return registry


def test_memory_tool_schema_hides_scope(tmp_path):
    registry = _memory_registry(tmp_path)

    for name in (
        "remember_user_memory",
        "list_user_memories",
        "forget_user_memory",
    ):
        tool_schema = registry.get(name).tool.tool_call_schema.model_json_schema()
        assert "memory_scope" not in tool_schema.get("properties", {})


def test_executor_overwrites_model_scope_with_trusted_state(tmp_path):
    registry = _memory_registry(tmp_path)
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "remember_user_memory",
                        "args": {
                            "memory_key": "language",
                            "content": {"value": "zh-CN"},
                            "memory_scope": {
                                "user_id": "attacker",
                                "tenant_id": "other",
                            },
                        },
                        "id": "remember-1",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "tool_context": ToolContext.GENERAL.value,
        "tool_artifacts": [],
        "last_tool_batch": [],
        "memory_scope": {"user_id": "user-a", "tenant_id": "tenant-a"},
    }

    result = tooling.execute_tools(state, registry=registry)

    assert json.loads(result["messages"][0].content)["success"] is True
    repository = registry.get("remember_user_memory").tool.func.__closure__[0].cell_contents
    assert repository.list_active(tenant_id="tenant-a", user_id="user-a")
    assert repository.list_active(tenant_id="other", user_id="attacker") == []


def test_memory_tools_fail_closed_without_scope(tmp_path):
    registry = _memory_registry(tmp_path)
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_user_memories",
                        "args": {},
                        "id": "list-1",
                        "type": "tool_call",
                    }
                ],
            )
        ],
        "tool_context": ToolContext.GENERAL.value,
        "tool_artifacts": [],
        "last_tool_batch": [],
        "memory_scope": {},
    }

    result = tooling.execute_tools(state, registry=registry)

    assert result["messages"][0].status == "error"
    assert "身份" in result["messages"][0].content