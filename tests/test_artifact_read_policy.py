import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai.chat_models.base import _convert_message_to_dict
from langgraph.checkpoint.memory import InMemorySaver

from src.agent import react_nodes, tooling
from src.agent.artifact_store import ArtifactAccessError, ArtifactNotFound, store_artifact
from src.agent.react_graph import build_react_graph
from src.tools import artifact_tools
from src.tools.registry import ToolContext, ToolRisk, build_builtin_registry, register_tool
from tests.test_tool_execution import _published_state
from tests.test_tool_extensibility import RecordingLlm


def _call(name, call_id, **args):
    return AIMessage(content="", tool_calls=[{
        "name": name, "id": call_id, "args": args, "type": "tool_call",
    }])


@pytest.mark.parametrize("mode", ["unpublished", "foreign", "forged", "disabled", "no-source"])
def test_reference_policy_denies_before_io(monkeypatch, tmp_path, mode):
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path))
    reference = store_artifact({"records": []}, request_id="request-1", tool_name="source", tool_call_id="source-1")
    state = {"request_id": "request-1", "tool_context": "general", **_published_state(reference)}
    artifact_id = reference["artifact_id"]
    if mode == "unpublished":
        state["published_artifact_refs"] = {}
    elif mode == "foreign":
        state["request_id"] = "request-2"
    elif mode == "forged":
        artifact_id = "fabricated-id"
    elif mode == "disabled":
        state["artifact_read_disabled"] = True
    else:
        state["tool_artifacts"] = []
    state["messages"] = [_call("read_tool_artifact", "read-1", artifact_id=artifact_id)]
    monkeypatch.setattr(artifact_tools, "load_artifact", lambda *args, **kwargs: pytest.fail("unexpected IO"))
    registry = build_builtin_registry((artifact_tools.register_artifact_tools,))

    result = tooling.execute_tools(state, registry=registry)

    assert json.loads(result["messages"][0].content)["error_code"] == "invalid_reference"
    assert result["artifact_read_disabled"] is True


@pytest.mark.parametrize("failure", [False, True], ids=["pagination", "missing-file"])
def test_graph_publishes_before_read_and_recovers_after_read_failure(monkeypatch, tmp_path, failure):
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_CONTEXT_WINDOW_TOKENS", "5000")
    monkeypatch.setenv("AGENT_CONTEXT_OUTPUT_TOKENS", "500")
    monkeypatch.setenv("AGENT_CONTEXT_SAFETY_TOKENS", "500")
    monkeypatch.setattr(react_nodes, "REACT_SYSTEM_PROMPT", "Use tool evidence to answer.")

    @tool("lookup_records")
    def lookup_records() -> dict:
        """Look up records."""
        return {"success": True, "records": [{"text": "source fact " * 500} for _ in range(50)]}

    def register_source(registry):
        register_tool(lookup_records, registry=registry, risk=ToolRisk.READ_ONLY, contexts={ToolContext.GENERAL})

    class Model:
        def __init__(self):
            self.bound_names = []
            self.step = 0
            self.reference = None

        def bind_tools(self, tools):
            self.bound_names.append({item.name for item in tools})
            assert tools
            return self

        def invoke(self, messages):
            pending = set()
            for message in messages:
                wire = _convert_message_to_dict(message)
                if isinstance(message, AIMessage):
                    pending.update(call["id"] for call in message.tool_calls)
                elif isinstance(message, ToolMessage):
                    assert wire["tool_call_id"] in pending
                    pending.remove(wire["tool_call_id"])
                    assert "additional_kwargs" not in wire
            assert not pending
            self.step += 1
            if self.step == 1:
                return _call("lookup_records", "source-1")
            if self.step == 2:
                payload = json.loads(next(message.content for message in messages if isinstance(message, ToolMessage)))
                assert payload["context_view"] == "artifact_index"
                self.reference = payload["artifact_ref"]
                if failure:
                    (tmp_path / f"{self.reference['artifact_id']}.json").unlink()
                return _call("read_tool_artifact", "read-1", artifact_id=self.reference["artifact_id"], key="records", limit=1)
            result = json.loads([message for message in messages if isinstance(message, ToolMessage)][-1].content)
            if failure:
                assert result["error_code"] == "unavailable"
            else:
                assert result["success"] is True
                if self.step == 3:
                    return _call("read_tool_artifact", "read-2", artifact_id=self.reference["artifact_id"], key="records", offset=1, limit=1)
            return AIMessage(content="Evidence-based answer with limitations.")

    model = Model()
    registry = build_builtin_registry((register_source, artifact_tools.register_artifact_tools))
    result = build_react_graph(registry=registry, llm_factory=lambda: model).invoke({"user_query": "lookup"})

    assert "read_tool_artifact" not in model.bound_names[0]
    assert "read_tool_artifact" in model.bound_names[1]
    assert ("read_tool_artifact" not in model.bound_names[-1]) is failure
    assert result["artifact_read_disabled"] is failure
    assert not result["react_tool_stop_reason"]
    assert result["published_artifact_refs"] == {model.reference["artifact_id"]: model.reference}
    assert result["final_answer"] == "Evidence-based answer with limitations."


def test_checkpoint_clears_reader_state_for_new_request(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path))
    from src.pec.tools import register_pec_tools

    monkeypatch.setattr("src.pec.tools.search_pec_knowledge_service", lambda query: {"success": True, "evidence": [{"text": "fact"}]})
    model = RecordingLlm([
        _call("search_pec_knowledge", "pec", query="history"),
        _call("read_tool_artifact", "read", artifact_id="invented"),
        AIMessage(content="first answer"), AIMessage(content="second answer"),
    ])
    registry = build_builtin_registry((register_pec_tools, artifact_tools.register_artifact_tools))
    graph = build_react_graph(registry=registry, llm_factory=lambda: model, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "same-thread"}}
    first = graph.invoke({"user_query": "history", "request_id": "first"}, config)
    assert first["artifact_read_disabled"] is True
    assert not first["published_artifact_refs"]
    second = graph.invoke({"user_query": "continue", "request_id": "second",
                           "published_artifact_refs": {"stale": {}}, "artifact_reads": ["old"]}, config)
    assert second["artifact_read_disabled"] is False
    assert second["published_artifact_refs"] == {}
    assert second["artifact_reads"] == []
    assert all("read_tool_artifact" not in names for names in model.bound_names)


@pytest.mark.parametrize("exception", [ArtifactNotFound("secret-id"), ArtifactAccessError("secret-path"), OSError("private-path"), ValueError("invalid-id")])
def test_expected_storage_errors_recover_without_exposing_details(monkeypatch, exception):
    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(artifact_tools, "load_artifact", fail)
    result = artifact_tools.read_tool_artifact.invoke({"artifact_id": "opaque"})
    assert result["error_code"] == "unavailable"
    assert str(exception) not in json.dumps(result)
    assert artifact_tools.apply_artifact_result({}, result) == {"artifact_read_disabled": True}


def test_unknown_reader_exception_still_stops(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_ARTIFACT_DIR", str(tmp_path))
    reference = store_artifact({}, request_id="request-1", tool_name="source", tool_call_id="source-1")

    def fail(*args, **kwargs):
        raise RuntimeError("unexpected defect")

    monkeypatch.setattr(artifact_tools, "load_artifact", fail)
    registry = build_builtin_registry((artifact_tools.register_artifact_tools,))
    result = react_nodes.execute_function_tools({
        "request_id": "request-1", "react_tool_context": "general", **_published_state(reference),
        "react_messages": [_call("read_tool_artifact", "read", artifact_id=reference["artifact_id"])],
    }, registry=registry)
    assert result["react_tool_stop_reason"]
    assert result["tool_artifacts"][-1]["result"]["error_type"] == "RuntimeError"


def test_reader_injected_publications_are_not_model_parameters():
    schema = artifact_tools.read_tool_artifact.tool_call_schema.model_fields
    assert "request_id" not in schema
    assert "published_references" not in schema