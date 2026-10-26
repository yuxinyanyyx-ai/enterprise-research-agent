import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from src.agent.react_graph import build_react_graph
from src.agent.skills import load_skill_catalog
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


def test_knowledge_guidance_reaches_agent_without_loading_tool(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path))
    evidence = {"text": "The project milestone is September 2027.",
                "source_file": "meeting.pptx", "location": "Slide 3"}
    monkeypatch.setattr(
        "src.pec.tools.search_pec_knowledge_service",
        lambda query: {"success": True, "evidence": [evidence]},
    )
    guidance = load_skill_catalog().get("knowledge").instructions

    class Model:
        def bind_tools(self, tools, *, tool_choice=None):
            assert {tool.name for tool in tools} <= {"search_pec_knowledge"}
            return self

        def invoke(self, messages):
            system = "\n".join(message.content for message in messages if isinstance(message, SystemMessage))
            assert system.count(guidance) == 1
            results = [message for message in messages if isinstance(message, ToolMessage)]
            if not results:
                return _call("search_pec_knowledge", "pec-1", query="project milestone")
            assert json.loads(results[-1].content)["evidence"] == [evidence]
            return AIMessage(content="September 2027; meeting.pptx, Slide 3.")

    registry = build_builtin_registry((register_pec_tools,))
    result = build_react_graph(registry=registry, llm_factory=Model).invoke({"user_query": "PEC project milestone"})
    assert [artifact["tool_name"] for artifact in result["tool_artifacts"]] == ["search_pec_knowledge"]
    assert result["final_answer"] == "September 2027; meeting.pptx, Slide 3."


def test_non_pec_answer_does_not_require_retrieval(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path))

    def unexpected_search(query):
        raise AssertionError("No PEC retrieval expected")

    monkeypatch.setattr("src.pec.tools.search_pec_knowledge_service", unexpected_search)
    registry = build_builtin_registry((register_pec_tools,))
    result = build_react_graph(
        registry=registry, llm_factory=lambda: FakeLlm([AIMessage(content="Hello.")]),
    ).invoke({"user_query": "Hello"})
    assert result["tool_artifacts"] == []
    assert result["final_answer"] == "Hello."


def test_default_registry_includes_all_pec_tools() -> None:
    registry = build_builtin_registry()
    expected_risks = {
        "search_pec_knowledge": ToolRisk.READ_ONLY,
        "get_meeting_index_status": ToolRisk.READ_ONLY,
        "update_meeting_index": ToolRisk.LOCAL_WRITE,
        "generate_project_slide": ToolRisk.LOCAL_WRITE,
        "modify_project_slide": ToolRisk.LOCAL_WRITE,
        "parse_pptx_structure": ToolRisk.READ_ONLY,
        "load_skill": ToolRisk.READ_ONLY,
    }
    for name, risk in expected_risks.items():
        definition = registry.get(name)
        assert definition.risk is risk
        assert ToolContext.GENERAL in definition.contexts
    assert set(registry.get("parse_pptx_structure").tool.args) == {"document_id"}


def test_newly_connected_pec_tools_call_services(monkeypatch, tmp_path) -> None:
    from src.pec.tools.index_tools import get_meeting_index_status, update_meeting_index
    from src.pec.tools.ppt_tools import parse_pptx_structure

    monkeypatch.setattr(
        "src.pec.tools.index_tools.get_meeting_index_status_service",
        lambda: "index status",
    )
    monkeypatch.setattr(
        "src.pec.tools.index_tools.update_meeting_index_service",
        lambda: "index updated",
    )
    monkeypatch.setattr(
        "src.pec.tools.ppt_tools.parse_pptx_structure_service",
        lambda file_path: f"parsed {file_path}",
    )
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    pptx_path = upload_dir / "meeting.pptx"
    pptx_path.write_bytes(b"pptx")
    monkeypatch.setattr(
        "src.pec.tools.ppt_tools.load_settings",
        lambda **kwargs: SimpleNamespace(
            upload_dir=upload_dir,
            result_dir=tmp_path / "results",
        ),
    )
    assert get_meeting_index_status.invoke({}) == "index status"
    assert update_meeting_index.invoke({}) == "index updated"
    assert parse_pptx_structure.func(
        "meeting-id",
        {"meeting-id": {"source_path": str(pptx_path)}},
    ) == f"parsed {pptx_path}"


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
    assert model.bound_choices[1:] == ["none", "none"]
    assert all(names == {"search_pec_knowledge"} for names in model.bound_names[1:])
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
    class Model:
        def bind_tools(self, tools, *, tool_choice=None):
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