import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from src.agent.react_graph import build_react_graph
from src.pec.tools import register_pec_tools
from src.tools.artifact_tools import register_artifact_tools
from src.tools.registry import (
    ToolContext,
    ToolResultStrategy,
    ToolRisk,
    build_builtin_registry,
)
from tests.test_react_graph import FakeLlm
from tests.test_tool_extensibility import RecordingLlm


def _call(name: str, call_id: str, **args):
    return AIMessage(content="", tool_calls=[{
        "name": name, "id": call_id, "args": args, "type": "tool_call",
    }])


def test_pec_provider_registers_read_only_tool() -> None:
    registry = build_builtin_registry((register_pec_tools,))
    definition = registry.get("search_pec_knowledge")
    assert definition.risk is ToolRisk.READ_ONLY
    assert ToolContext.GENERAL in definition.contexts
    assert definition.parallel_safe is True
    assert definition.result_strategy is ToolResultStrategy.INLINE_COMPACT


def test_pec_tool_runs_through_outer_graph(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.pec.tools.search_pec_knowledge_service",
        lambda query: {"success": True, "context_summary": f"PEC summary for {query}"},
    )
    registry = build_builtin_registry((register_pec_tools,))
    responses = [
        _call("search_pec_knowledge", "pec-1", query="historical decision"),
        AIMessage(content="answer"),
    ]
    result = build_react_graph(
        registry=registry,
        llm_factory=lambda: FakeLlm(responses),
    ).invoke({"user_query": "historical decision"})
    assert result["tool_artifacts"][0]["result"] == {
        "success": True, "context_summary": "PEC summary for historical decision",
    }
    assert result["final_answer"] == "answer"


def test_pec_answer_survives_invalid_artifact_read(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setattr(
        "src.pec.tools.search_pec_knowledge_service",
        lambda query: {"success": True, "evidence": [{"text": "reported enrollment"}]},
    )
    responses = [
        _call("search_pec_knowledge", "pec-1", query="enrollment"),
        _call("read_tool_artifact", "read-1", artifact_id="unknown"),
        AIMessage(content="Answer based on reported enrollment."),
    ]
    registry = build_builtin_registry((register_pec_tools, register_artifact_tools))
    result = build_react_graph(
        registry=registry, llm_factory=lambda: FakeLlm(responses),
    ).invoke({"user_query": "enrollment"})

    assert result["final_answer"] == "Answer based on reported enrollment."
    assert not result.get("react_tool_stop_reason")
    assert result["tool_artifacts"][0]["result"]["evidence"]


def test_successful_pec_hit_blocks_rephrased_repeat(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path))
    queries = []

    def search(query):
        queries.append(query)
        return {"success": True, "evidence": [{"text": "fact", "truncated": True}]}

    monkeypatch.setattr("src.pec.tools.search_pec_knowledge_service", search)
    registry = build_builtin_registry((register_pec_tools, register_artifact_tools))
    model = RecordingLlm([
        _call("search_pec_knowledge", "first", query="enrollment"),
        _call("search_pec_knowledge", "repeat", query="enrollment details"),
        AIMessage(content="Answer from existing evidence."),
    ])
    result = build_react_graph(registry=registry, llm_factory=lambda: model).invoke({"user_query": "enrollment"})
    assert queries == ["enrollment"]
    assert "search_pec_knowledge" in model.bound_names[0]
    assert all("search_pec_knowledge" not in names for names in model.bound_names[1:])
    assert result["final_answer"] == "Answer from existing evidence."


def test_empty_pec_result_allows_only_one_supplement():
    definition = build_builtin_registry((register_pec_tools,)).get("search_pec_knowledge")
    empty = {"tool_name": "search_pec_knowledge", "result": {"success": True, "evidence_count": 0}}
    assert definition.availability({"tool_artifacts": [empty]})
    assert not definition.availability({"tool_artifacts": [empty, empty]})


def test_real_service_result_reaches_model_inline_after_600_chars(monkeypatch, tmp_path):
    from src.pec import knowledge_service

    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path))
    text = "background " * 600 + "China milestone: September 2027."
    monkeypatch.setattr(knowledge_service, "search_chunks", lambda *args, **kwargs: [
        SimpleNamespace(text=text, source_ref="meeting.pptx", loc="Slide 1"),
    ])
    monkeypatch.setattr(knowledge_service, "search_topics", lambda query: {"hits": []})

    class Model:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            results = [message for message in messages if isinstance(message, ToolMessage)]
            if not results:
                return _call("search_pec_knowledge", "pec-1", query="China milestone")
            content = results[-1].content
            payload = json.loads(content)
            assert 5000 < len(content.encode("utf-8")) <= 20000
            assert "context" not in payload
            assert payload["evidence"][0]["text"] == text
            assert "read_required" not in payload
            return AIMessage(content="September 2027; meeting.pptx, Slide 1.")

    registry = build_builtin_registry((register_pec_tools, register_artifact_tools))
    result = build_react_graph(registry=registry, llm_factory=Model).invoke({"user_query": "China milestone"})
    assert result["final_answer"].startswith("September 2027")
    assert result["published_artifact_refs"] == {}