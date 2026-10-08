from __future__ import annotations

import json

from langchain_core.messages import AIMessage

from src.agent.react_graph import build_react_graph
from src.agent_memory.repository import MemoryRepository


class RecordingBoundModel:
    def __init__(self, responses: list[AIMessage], prompts: list[list[object]]) -> None:
        self.responses = responses
        self.prompts = prompts

    def invoke(self, messages):
        self.prompts.append(list(messages))
        return self.responses.pop(0)


class RecordingLlm:
    def __init__(self, responses: list[AIMessage], prompts: list[list[object]]) -> None:
        self.responses = responses
        self.prompts = prompts

    def bind_tools(self, _tools):
        return RecordingBoundModel(self.responses, self.prompts)


def _call(name: str, args: dict, call_id: str) -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _state(query: str) -> dict:
    return {
        "user_query": query,
        "warnings": [],
        "memory_scope": {"tenant_id": "tenant-a", "user_id": "user-a"},
    }


def test_explicit_memory_survives_new_thread_and_can_be_forgotten(tmp_path):
    repository = MemoryRepository(f"sqlite:///{tmp_path / 'memory.db'}")
    repository.initialize_schema()
    prompts: list[list[object]] = []

    remember_responses = [
        AIMessage(
            content="",
            tool_calls=[
                _call(
                    "remember_user_memory",
                    {"memory_key": "language", "content": {"value": "zh-CN"}},
                    "remember-1",
                )
            ],
        ),
        AIMessage(content="已记住。"),
    ]
    graph = build_react_graph(
        llm_factory=lambda: RecordingLlm(remember_responses, prompts),
        memory_repository=repository,
        memory_limit=2,
    )
    graph.invoke(_state("请记住我偏好中文"), config={"configurable": {"thread_id": "thread-1"}})

    list_responses = [
        AIMessage(
            content="",
            tool_calls=[_call("list_user_memories", {}, "list-1")],
        ),
        AIMessage(content="你的语言偏好是中文。"),
    ]
    graph = build_react_graph(
        llm_factory=lambda: RecordingLlm(list_responses, prompts),
        memory_repository=repository,
        memory_limit=2,
    )
    listed = graph.invoke(
        _state("我的语言偏好是什么"),
        config={"configurable": {"thread_id": "thread-2"}},
    )

    assert listed["final_answer"] == "你的语言偏好是中文。"
    assert any("zh-CN" in str(message.content) for message in prompts[2])
    assert json.loads(listed["react_messages"][-2].content)["memories"][0]["memory_key"] == "language"

    forget_responses = [
        AIMessage(
            content="",
            tool_calls=[
                _call("forget_user_memory", {"memory_key": "language"}, "forget-1")
            ],
        ),
        AIMessage(content="已忘记。"),
    ]
    graph = build_react_graph(
        llm_factory=lambda: RecordingLlm(forget_responses, prompts),
        memory_repository=repository,
    )
    forgotten = graph.invoke(
        _state("请忘记我的语言偏好"),
        config={"configurable": {"thread_id": "thread-3"}},
    )

    assert forgotten["final_answer"] == "已忘记。"
    assert repository.list_active(tenant_id="tenant-a", user_id="user-a") == []


def test_plain_answer_does_not_write_memory(tmp_path):
    repository = MemoryRepository(f"sqlite:///{tmp_path / 'memory.db'}")
    repository.initialize_schema()
    graph = build_react_graph(
        llm_factory=lambda: RecordingLlm([AIMessage(content="普通回答")], []),
        memory_repository=repository,
    )

    result = graph.invoke(_state("你好"))

    assert result["final_answer"] == "普通回答"
    assert repository.list_active(tenant_id="tenant-a", user_id="user-a") == []
