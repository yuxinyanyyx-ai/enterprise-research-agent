import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.agent import audit_log


@pytest.fixture
def audit_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "LOG_DIR", tmp_path / "audit")
    yield audit_log.LOG_DIR
    with audit_log._lock:
        if audit_log._handler is not None:
            audit_log._handler.close()
        audit_log._handler = None
        audit_log._handler_path = None


def _events(directory):
    return [json.loads(line) for path in directory.glob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()]


def test_trace_pairs_events_without_payloads(audit_directory):
    call = {"id": "call-1", "name": "search_dmf", "args": {"token": "private-token"}}
    with audit_log.trace_operation({"request_id": "request-1"}, call=call):
        pass
    started, finished = _events(audit_directory)
    assert started["event"] == "execution.started"
    assert started["execution_id"] == finished["execution_id"]
    assert finished["request_id"] == "request-1"
    assert finished["tool_call_id"] == "call-1"
    assert finished["duration_ms"] >= 0
    assert "private-token" not in json.dumps([started, finished])


def test_exception_is_preserved_without_logging_its_message(audit_directory):
    with pytest.raises(ValueError, match="private@example.com"):
        with audit_log.trace_operation({}):
            raise ValueError("private@example.com")
    finished = _events(audit_directory)[-1]
    assert finished["status"] == "error"
    assert finished["error_type"] == "ValueError"
    assert "private@example.com" not in json.dumps(finished)


def test_parallel_events_are_complete_json_lines(audit_directory):
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda index: audit_log.write_event(
            "test", {"request_id": str(index)}), range(24)))
    assert len(_events(audit_directory)) == 24


def test_log_failure_does_not_fail_operation(tmp_path, monkeypatch):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    monkeypatch.setattr(audit_log, "LOG_DIR", blocked)
    with audit_log.trace_operation({}):
        pass


@pytest.mark.parametrize("failure", [False, True])
def test_executor_writes_actual_outcome(audit_directory, monkeypatch, failure):
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool
    from src.agent import tooling
    from src.tools.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk

    @tool("local_test")
    def local_test() -> dict:
        """Return a local test result."""
        if failure:
            raise ValueError("private-token")
        return {"success": True, "secret": "private-token"}

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        tool=local_test, risk=ToolRisk.READ_ONLY,
        contexts=frozenset({ToolContext.GENERAL}),
    ))
    monkeypatch.setattr(tooling, "load_builtin_tools", lambda: registry)
    tooling.execute_tools({
        "request_id": "request-1",
        "messages": [AIMessage(content="", tool_calls=[{
            "name": "local_test", "id": "call-1", "args": {}, "type": "tool_call",
        }])],
    })
    events = _events(audit_directory)
    assert events[-1]["status"] == ("error" if failure else "completed")
    assert "private-token" not in json.dumps(events)


def test_files_rotate_with_bounded_backups(audit_directory, monkeypatch):
    monkeypatch.setattr(audit_log, "MAX_BYTES", 1024)
    for index in range(40):
        audit_log.write_event("rotation", {"request_id": str(index)})
    paths = list(audit_directory.iterdir())
    assert len(paths) == audit_log.BACKUP_COUNT + 1
    for path in paths:
        assert path.stat().st_size <= 1024
        for line in path.read_text(encoding="utf-8").splitlines():
            assert json.loads(line)["event"] == "rotation"


@pytest.mark.parametrize("decision", ["confirm", "reject"])
def test_outer_workflow_logs_confirmation_and_resume(audit_directory, monkeypatch, decision):
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from src.agent import domain_workflows, nodes
    from src.agent.react_graph import build_react_graph

    monkeypatch.setattr(domain_workflows, "_parse", lambda state, domain: {
        "data_source": "document", "query_document_conditions": True,
        "needs_clarification": False, "requested_outputs": ["result"],
    })
    monkeypatch.setattr(nodes, "extract_document_query", lambda state: {
        "merged_document_query": {"queries": [{
            "ingredients": ["example"],
            "sources": [{"document_id": "doc-1", "file_name": "private.pdf"}],
        }]},
    })
    query_calls = []

    class Service:
        def execute_confirmed_query(self, query):
            query_calls.append(query)
            return {"success": True, "results": []}

    class Model:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            if any(getattr(message, "name", "") == "run_document_dmf_workflow" for message in messages):
                return AIMessage(content="completed")
            return AIMessage(content="", tool_calls=[{
                "id": "handoff-1", "name": "run_document_dmf_workflow",
                "args": {}, "type": "tool_call",
            }])

    monkeypatch.setattr(nodes, "DocumentDMFService", Service)
    graph = build_react_graph(checkpointer=InMemorySaver(), llm_factory=Model)
    config = {"configurable": {"thread_id": "audit-thread"}}
    paused = graph.invoke({"user_query": "document query", "request_id": "audit-request"}, config)
    assert paused["__interrupt__"]
    assert not query_calls
    graph.invoke(Command(resume={"action": decision}), config)
    events = _events(audit_directory)
    assert all(event["request_id"] == "audit-request" for event in events)
    assert any(event["event"] == "workflow.handoff" for event in events)
    assert any(event["event"] == "document.resumed" and event["decision"] == decision
               for event in events)
    assert events[-1]["status"] == ("completed" if decision == "confirm" else "cancelled")
    assert len(query_calls) == (1 if decision == "confirm" else 0)
    assert "private.pdf" not in json.dumps(events)