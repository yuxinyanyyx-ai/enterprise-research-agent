from langgraph.checkpoint.memory import InMemorySaver
import pytest

from src.agent import nodes
from src.agent.domain_workflows import build_watchlist_workflow
from tests.domain_harness import DomainHarness
from src.schemas.intent import ResearchIntent
from tests.test_agent_watchlist import StructuredIntentModel
from tests.test_dmf_watchlist_service import make_service


def intent(**values):
    return ResearchIntent(data_source="watchlist", watchlist_action="configure_notifications", **values)


def setup_graph(tmp_path, monkeypatch, parsed):
    repository, service = make_service(tmp_path, [])
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)
    monkeypatch.setattr(nodes, "create_apollo_llm", lambda: StructuredIntentModel(parsed))
    return repository, service, DomainHarness(build_watchlist_workflow(checkpointer=InMemorySaver()))


def test_existing_email_enable_does_not_require_request_email(tmp_path, monkeypatch):
    repository, service, graph = setup_graph(tmp_path, monkeypatch, intent(dmf_no="DMF-001", watchlist_notification_enabled=True))
    row = service.add("DMF-001", 48, notification_emails=["someone@example.com"])
    result = graph.invoke({"user_query": "开启 DMF-001 邮件通知"}, {"configurable": {"thread_id": "enable"}})
    assert repository.get(row.id).notification_enabled is True
    assert result["needs_clarification"] is False
    assert "未发送邮件" in result["final_answer"]
    assert "someone@example.com" not in result["final_answer"]


def test_missing_email_followup_retains_target(tmp_path, monkeypatch):
    repository, service, graph = setup_graph(tmp_path, monkeypatch, intent(dmf_no="DMF-001", watchlist_notification_enabled=True))
    row = service.add("DMF-001")
    config = {"configurable": {"thread_id": "followup"}}
    first = graph.invoke({"user_query": "开启 DMF-001 通知"}, config)
    assert first["needs_clarification"] is True
    assert repository.get(row.id).notification_enabled is False
    monkeypatch.setattr(nodes, "create_apollo_llm", lambda: (_ for _ in ()).throw(AssertionError("No LLM needed")))
    second = graph.invoke({"user_query": "person@example.com"}, config)
    assert second["needs_clarification"] is False
    assert second["pending_intent"] == {}
    assert repository.get(row.id).notification_emails == ["person@example.com"]
    assert repository.get(row.id).notification_enabled is True


def test_create_clarifies_mode_before_writing(tmp_path, monkeypatch):
    parsed = ResearchIntent(data_source="watchlist", watchlist_action="add", dmf_no="DMF-001",
                            watchlist_notification_enabled=True, watchlist_notification_emails=["user@example.com"])
    repository, service, graph = setup_graph(tmp_path, monkeypatch, parsed)
    config = {"configurable": {"thread_id": "create"}}
    first = graph.invoke({"user_query": "关注 DMF-001，邮件通知 user@example.com"}, config)
    assert first["needs_clarification"]
    assert repository.list() == []
    second = graph.invoke({"user_query": "每周汇总"}, config)
    assert second["needs_clarification"] is False
    assert repository.list()[0].notification_mode == "weekly_digest"
    assert repository.list()[0].interval_hours == 24


def test_mode_only_disable_and_clear_are_partial_updates(tmp_path, monkeypatch):
    repository, service = make_service(tmp_path, [])
    row = service.add("DMF-001", 48, notification_enabled=True,
                      notification_emails=["user@example.com"], notification_mode="immediate")
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)
    base = {"watchlist_action": "configure_notifications", "dmf_no": "DMF-001"}
    nodes.manage_watchlist({**base, "watchlist_notification_mode": "weekly_digest"})
    updated = repository.get(row.id)
    assert updated.notification_mode == "weekly_digest"
    assert updated.notification_enabled is True
    assert updated.interval_hours == 48
    failed = nodes.manage_watchlist({**base, "watchlist_notification_emails": []})
    assert failed["needs_clarification"] is True
    assert repository.get(row.id).notification_emails == ["user@example.com"]
    nodes.manage_watchlist({**base, "watchlist_notification_enabled": False})
    assert repository.get(row.id).notification_emails == ["user@example.com"]
    nodes.manage_watchlist({**base, "watchlist_notification_emails": []})
    assert repository.get(row.id).notification_emails == []


@pytest.mark.parametrize("query", ["全部关注项开启通知", "立即发邮件", "转发到 user@example.com"])
def test_unsupported_request_cannot_be_resumed(monkeypatch, query):
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: (_ for _ in ()).throw(AssertionError("No service")))
    parsed = intent(dmf_no="DMF-001", watchlist_notification_enabled=True)
    state = {"user_query": query, **nodes._validate_intent(parsed, {"user_query": query})}
    result = nodes.ask_clarification(state)
    assert result["pending_intent"] == {}
    assert "暂未开放" in result["final_answer"]


def test_append_requires_complete_list():
    parsed = intent(dmf_no="DMF-001", watchlist_notification_emails=["new@example.com"])
    result = nodes._validate_intent(parsed, {"user_query": "给 DMF-001 追加邮箱 new@example.com"})
    assert result["needs_clarification"]
    assert "完整" in result["clarification_question"]


@pytest.mark.parametrize("query,records,expected", [
    ("关闭这个的通知", ["DMF-001"], "DMF-001"),
    ("关闭通知", ["DMF-001"], ""),
    ("关闭这个的通知", ["DMF-001", "DMF-002"], ""),
])
def test_configuration_uses_only_explicit_unique_reference(query, records, expected):
    state = {"user_query": query, "dmf_results": {"success": True, "results": [
        {"records": [{"dmf_no": number} for number in records]}
    ]}}
    result = nodes._validate_intent(intent(watchlist_notification_enabled=False), state)
    assert result["dmf_no"] == expected
    assert result["needs_clarification"] is (not bool(expected))


def test_new_explicit_query_clears_pending_notification(monkeypatch):
    from langchain_core.messages import AIMessage
    from src.agent.react_graph import build_react_graph
    from tests.test_react_graph import FakeLlm
    from tests.test_react_composition import call, result_data
    monkeypatch.setattr("src.tools.dmf_tools.search_dmf_queries", lambda **kwargs: result_data())
    responses = [call("search_dmf", "new", dmf_no="OTHER"), AIMessage(content="查询完成")]
    result = build_react_graph(llm_factory=lambda: FakeLlm(responses)).invoke({
        "user_query": "查询 OTHER", "domain_pending": {"watchlist": intent(dmf_no="DMF-001", watchlist_notification_enabled=True).model_dump()},
    })
    assert result["domain_pending"] == {}


def test_invalid_email_error_is_masked_and_does_not_write(tmp_path, monkeypatch):
    repository, service = make_service(tmp_path, [])
    row = service.add("DMF-001")
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)
    result = nodes.manage_watchlist({"watchlist_action": "configure_notifications", "dmf_no": "DMF-001",
                                    "watchlist_notification_emails": ["private-invalid-address"]})
    assert result["needs_clarification"]
    assert "private-invalid-address" not in result["final_answer"]
    assert repository.get(row.id).notification_emails == []


def test_duplicate_add_does_not_update_configuration(tmp_path, monkeypatch):
    repository, service = make_service(tmp_path, [])
    row = service.add("DMF-001")
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)
    result = nodes.manage_watchlist({"watchlist_action": "add", "dmf_no": "DMF-001",
                                    "watchlist_notification_enabled": True,
                                    "watchlist_notification_emails": ["user@example.com"],
                                    "watchlist_notification_mode": "immediate"})
    assert "未完成" in result["final_answer"]
    assert repository.get(row.id).notification_enabled is False


def test_list_displays_notification_without_full_recipient(tmp_path, monkeypatch):
    repository, service = make_service(tmp_path, [])
    service.add("DMF-001", notification_emails=["person@example.com"], notification_mode="weekly_digest")
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)
    result = nodes.manage_watchlist({"watchlist_action": "list"})
    assert "每周汇总" in result["final_answer"]
    assert "通知：关闭" in result["final_answer"]
    assert "person@example.com" not in result["final_answer"]