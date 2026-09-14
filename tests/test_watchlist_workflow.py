from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver

from src.agent import nodes
from src.agent.domain_workflows import build_watchlist_workflow
from src.schemas.domain_intent import WatchlistIntent
from tests.test_dmf_watchlist_service import make_service


class DomainModel:
    def __init__(self, value):
        self.value = value

    def with_structured_output(self, schema):
        assert schema is WatchlistIntent
        return self

    def invoke(self, messages):
        return WatchlistIntent(**self.value)


def test_notification_short_reply_retains_target_without_llm(tmp_path, monkeypatch):
    repository, service = make_service(tmp_path, [])
    row = service.add("DMF-001")
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)
    monkeypatch.setattr(nodes, "create_apollo_llm", lambda: DomainModel({
        "watchlist_action": "configure_notifications", "dmf_no": "DMF-001", "watchlist_notification_enabled": True,
    }))
    graph = build_watchlist_workflow(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "notification"}}
    first = graph.invoke({"workflow_input": {"user_query": "开启 DMF-001 通知", "request_id": "first"}}, config)["workflow_output"]
    assert first["result"]["status"] == "needs_clarification"
    assert not repository.get(row.id).notification_enabled
    monkeypatch.setattr(nodes, "create_apollo_llm", lambda: (_ for _ in ()).throw(AssertionError("No LLM needed")))
    second = graph.invoke({"workflow_input": {"user_query": "person@example.com", "request_id": "second",
                         "pending_intent": first["updates"]["pending_intent"]}}, config)["workflow_output"]
    assert second["result"]["status"] == "completed"
    assert second["updates"]["pending_intent"] == {}
    assert repository.get(row.id).notification_enabled
    assert repository.get(row.id).notification_emails == ["person@example.com"]
    assert "person@example.com" not in second["result"]["message"]


def test_same_request_write_reuses_result(tmp_path, monkeypatch):
    repository, service = make_service(tmp_path, [])
    calls = []
    original = service.add

    def add(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "add", add)
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)
    monkeypatch.setattr(nodes, "create_apollo_llm", lambda: DomainModel({"watchlist_action": "add", "dmf_no": "DMF-001"}))
    graph = build_watchlist_workflow()
    payload = {"user_query": "关注 DMF-001", "request_id": "same"}
    first = graph.invoke({"workflow_input": payload})["workflow_output"]
    second = graph.invoke({"workflow_input": {**payload, "operation_ledger": first["updates"]["operation_ledger"]}})["workflow_output"]
    assert len(calls) == 1
    assert second["result"]["status"] == "completed"
    assert len(repository.list()) == 1


def test_failed_manual_check_is_not_completed(monkeypatch):
    service = SimpleNamespace(
        resolve=lambda **kwargs: SimpleNamespace(id="watch-1", dmf_no="DMF-001"),
        run_manual=lambda *args, **kwargs: SimpleNamespace(status=SimpleNamespace(value="query_failed"), error_message=None, warnings=[]),
    )
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)
    monkeypatch.setattr(nodes, "create_apollo_llm", lambda: DomainModel({"watchlist_action": "run", "dmf_no": "DMF-001"}))
    output = build_watchlist_workflow().invoke({"workflow_input": {"user_query": "检查 DMF-001", "request_id": "run"}})["workflow_output"]
    assert output["result"]["status"] == "failed"
    assert output["result"]["data"]["watchlist_result"]["run_status"] == "query_failed"


def test_bulk_request_never_calls_service(monkeypatch):
    monkeypatch.setattr(nodes, "create_apollo_llm", lambda: DomainModel({"watchlist_action": "remove", "dmf_no": "DMF-001"}))
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: (_ for _ in ()).throw(AssertionError("No side effect")))
    output = build_watchlist_workflow().invoke({"workflow_input": {"user_query": "删除全部关注"}})["workflow_output"]
    assert output["result"]["status"] == "rejected"
    assert output["updates"]["pending_intent"] == {}