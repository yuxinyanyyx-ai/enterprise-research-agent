from __future__ import annotations

import json

import httpx
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from src.agent.react_graph import build_react_graph
from src.agent.react_nodes import build_react_agent_node
from src.pec.tools.tools import register_pec_tools
from src.tools.registry import ToolContext, ToolRegistry, ToolRisk, register_tool


def test_final_answer_preserves_tool_protocol_without_reenabling_search(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "src.pec.tools.search_pec_knowledge_service",
        lambda query: {"success": True, "evidence": [
            {"text": "Milestone: September 2027.", "source_file": "meeting.pptx", "location": "Slide 3"},
        ]},
    )
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            message = {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": "pec-1", "type": "function",
                    "function": {"name": "search_pec_knowledge", "arguments": '{"query":"milestone"}'},
                }],
            }
        else:
            message = {"role": "assistant", "content": "September 2027; meeting.pptx, Slide 3."}
        return httpx.Response(200, json={
            "id": "offline-response", "object": "chat.completion", "model": "offline",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if len(requests) == 1 else "stop"}],
        })

    registry = ToolRegistry()
    register_pec_tools(registry)
    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        model = ChatOpenAI(
            api_key="offline-not-a-credential", model="offline", max_retries=0,
            http_client=http_client,
        )
        result = build_react_graph(registry=registry, llm_factory=lambda: model).invoke({
            "user_query": "PEC milestone", "react_max_tool_rounds": 1,
        })

    assert result["final_answer"] == "September 2027; meeting.pptx, Slide 3."
    assert len(result["tool_artifacts"]) == 1
    final = requests[1]
    assert final.get("tools"), "Tool history must not be sent with an empty tools declaration"
    assert final["tool_choice"] == "none"
    assert {tool["function"]["name"] for tool in final["tools"]} == {"search_pec_knowledge"}
    roles = [message["role"] for message in final["messages"]]
    assert roles[-1] == "tool"
    first_conversation = next(i for i, role in enumerate(roles) if role != "system")
    assert "system" not in roles[first_conversation:]
    tool_result = final["messages"][-1]
    assert tool_result["tool_call_id"] == "pec-1"
    assert json.loads(tool_result["content"])["evidence"][0]["text"] == "Milestone: September 2027."
    assert len(requests) == 2


def test_no_tools_no_history_omits_tool_parameters():
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "offline-response", "object": "chat.completion", "model": "offline",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
        model = ChatOpenAI(api_key="offline-not-a-credential", model="offline", max_retries=0, http_client=http_client)
        result = build_react_graph(registry=ToolRegistry(), llm_factory=lambda: model).invoke({"user_query": "Hello"})
    assert result["final_answer"] == "Hello"
    assert "tools" not in requests[0]
    assert "tool_choice" not in requests[0]


def test_final_history_schemas_count_toward_input_budget(monkeypatch):
    monkeypatch.setenv("AGENT_CONTEXT_WINDOW_TOKENS", "3000")
    monkeypatch.setenv("AGENT_CONTEXT_OUTPUT_TOKENS", "500")
    monkeypatch.setenv("AGENT_CONTEXT_SAFETY_TOKENS", "500")
    monkeypatch.setattr("src.agent.react_nodes.build_system_prompt", lambda *args, **kwargs: "Answer.")

    @tool
    def completed_search() -> dict:
        """Search evidence."""
        raise AssertionError("Completed search must not execute")

    completed_search.description = "Schema detail. " * 3000
    registry = ToolRegistry()
    register_tool(
        completed_search, registry=registry, contexts={ToolContext.GENERAL},
        risk=ToolRisk.READ_ONLY, availability=lambda state: False,
    )
    evidence = ToolMessage(content="Complete evidence.", name="completed_search", tool_call_id="done")
    messages = [
        HumanMessage(content="Question"), AIMessage(content="", tool_calls=[{
            "id": "done", "name": "completed_search", "args": {}, "type": "tool_call",
        }]), evidence,
    ]

    def no_model():
        raise AssertionError("Over-budget request must not create or call a model")

    result = build_react_agent_node(registry=registry, llm_factory=no_model)({"react_messages": messages})
    assert result["failure_code"] == "context_budget_exceeded"
    assert result["react_tool_stop_reason"] == "input_budget_exceeded"
    assert "可用输入预算 2000 Token" in result["react_messages"][0].content
    assert messages[-1] is evidence
    assert evidence.content == "Complete evidence."
