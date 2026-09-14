from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.domain_workflows import build_watchlist_workflow
from tests.domain_harness import DomainHarness
from src.schemas.domain_intent import WatchlistIntent
from src.agent import nodes
from src.dmf_watchlist.repository import WatchlistNotFoundError
from src.dmf_watchlist.service import WatchlistUnavailableError
from src.schemas.intent import ResearchIntent
from src.schemas.watchlist import WatchlistRunStatus, WatchlistStatus


class StructuredIntentModel:
    def __init__(self, intent: ResearchIntent) -> None:
        self.intent = intent

    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, messages):
        return self.schema.model_validate({key: value for key, value in self.intent.model_dump().items() if key in self.schema.model_fields})


class FakeWatchlistService:
    def __init__(self) -> None:
        self.calls = []
        self.row = SimpleNamespace(
            id="watch-1",
            dmf_no="DMF-001",
            status=WatchlistStatus.ACTIVE,
            interval_hours=24,
            next_run_at=None,
        )

    def add(self, dmf_no, interval_hours=24):
        self.calls.append(("add", dmf_no, interval_hours))
        return self.row

    def remove(self, **target):
        self.calls.append(("remove", target))
        return self.row

    def list_watchlists(self):
        self.calls.append(("list",))
        return [self.row]

    def resolve(self, **target):
        self.calls.append(("resolve", target))
        return self.row

    def run_manual(self, watchlist_id, *, idempotency_key=None):
        self.calls.append(("run", watchlist_id, idempotency_key))
        return SimpleNamespace(
            status=WatchlistRunStatus.NO_CHANGE,
            error_message=None,
            warnings=[],
        )

    def list_events(self, **target):
        self.calls.append(("events", target))
        return self.row, []

    def acknowledge_event(self, event_id, **target):
        self.calls.append(("ack", event_id, target))
        return SimpleNamespace(id=event_id, status=SimpleNamespace(value="acknowledged"))


def test_real_graph_routes_watchlist_add_to_manage_node(monkeypatch) -> None:
    service = FakeWatchlistService()
    intent = ResearchIntent(
        data_source="watchlist",
        watchlist_action="add",
        dmf_no="DMF-001",
    )
    monkeypatch.setattr(nodes, "create_apollo_llm", lambda: StructuredIntentModel(intent))
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)
    graph = build_watchlist_workflow(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "watchlist-add"}}

    updates = list(
        graph.stream(
            {"workflow_input": {"user_query": "关注 DMF-001", "request_id": "request-1"}},
            config=config,
            stream_mode="updates",
        )
    )
    state = graph.get_state(config).values

    assert [node for update in updates for node in update] == [
        "initialize", "parse", "execute", "finish",
    ]
    assert service.calls == [("add", "DMF-001", 24)]


@pytest.mark.parametrize("interval_hours", [0, 169])
def test_watchlist_add_rejects_interval_outside_service_range(
    monkeypatch, interval_hours: int
) -> None:
    service = FakeWatchlistService()
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)
    graph = DomainHarness(build_watchlist_workflow(checkpointer=InMemorySaver()))
    intent = ResearchIntent(
        data_source="watchlist",
        watchlist_action="add",
        dmf_no="DMF-001",
        watchlist_interval_hours=interval_hours,
    )
    monkeypatch.setattr(nodes, "create_apollo_llm", lambda: StructuredIntentModel(intent))

    result = graph.invoke(
        {
            "user_query": f"每 {interval_hours} 小时关注 DMF-001",
            "request_id": "request-invalid-interval",
            "warnings": [],
        },
        config={"configurable": {"thread_id": f"invalid-{interval_hours}"}},
    )

    assert result["needs_clarification"] is True
    assert "1 到 168 小时" in result["final_answer"]
    assert service.calls == []


def test_high_impact_watchlist_request_is_blocked_before_service(monkeypatch) -> None:
    intent = ResearchIntent(
        data_source="watchlist",
        watchlist_action="remove",
        dmf_no="DMF-001",
    )
    monkeypatch.setattr(nodes, "create_apollo_llm", lambda: StructuredIntentModel(intent))
    monkeypatch.setattr(
        nodes,
        "create_watchlist_service",
        lambda: (_ for _ in ()).throw(AssertionError("service must not be called")),
    )
    graph = DomainHarness(build_watchlist_workflow())

    result = graph.invoke({"user_query": "删除全部关注", "warnings": []})

    assert "批量、高影响或外发操作" in result["final_answer"]


def test_add_uses_only_unique_dmf_number_from_active_results() -> None:
    intent = ResearchIntent(data_source="watchlist", watchlist_action="add")
    state = {
        "user_query": "关注刚才结果",
        "dmf_results": {
            "results": [
                {
                    "records": [
                        {"dmf_no": "DMF-001"},
                        {"dmf_no": "dmf-001"},
                    ]
                }
            ]
        },
    }

    validated = nodes._validate_intent(intent, state)

    assert validated["needs_clarification"] is False
    assert validated["dmf_no"] == "DMF-001"


def test_add_with_multiple_recent_dmf_numbers_requires_clarification() -> None:
    intent = ResearchIntent(data_source="watchlist", watchlist_action="add")
    state = {
        "user_query": "关注刚才结果",
        "dmf_results": {
            "results": [
                {"records": [{"dmf_no": "DMF-001"}, {"dmf_no": "DMF-002"}]}
            ]
        },
    }

    validated = nodes._validate_intent(intent, state)

    assert validated["needs_clarification"] is True
    assert "唯一一条" in validated["clarification_question"]


def test_manual_run_uses_stable_request_idempotency_key(monkeypatch) -> None:
    service = FakeWatchlistService()
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)

    result = nodes.manage_watchlist(
        {
            "watchlist_action": "run",
            "watchlist_id": "watch-1",
            "request_id": "request-123",
        }
    )

    assert service.calls == [
        ("resolve", {"watchlist_id": "watch-1", "dmf_no": ""}),
        ("run", "watch-1", "agent:request-123:watch-1"),
    ]
    assert "no_change" in result["final_answer"]


def test_watchlist_unavailable_returns_public_message(monkeypatch) -> None:
    monkeypatch.setattr(
        nodes,
        "create_watchlist_service",
        lambda: (_ for _ in ()).throw(WatchlistUnavailableError()),
    )

    result = nodes.manage_watchlist({"watchlist_action": "list"})

    assert result["final_answer"] == "DMF 团队关注清单暂未启用，请联系管理员。"


def test_watchlist_not_found_does_not_expose_target_details(monkeypatch) -> None:
    service = FakeWatchlistService()
    service.remove = lambda **target: (_ for _ in ()).throw(
        WatchlistNotFoundError("secret-target")
    )
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)

    result = nodes.manage_watchlist(
        {"watchlist_action": "remove", "watchlist_id": "watch-missing"}
    )

    assert result["final_answer"] == "未找到指定的团队关注项或关注事件。"
    assert "secret-target" not in result["final_answer"]


def test_unexpected_watchlist_failure_is_logged_without_exposing_details(
    monkeypatch, caplog
) -> None:
    service = FakeWatchlistService()
    service.list_watchlists = lambda: (_ for _ in ()).throw(
        RuntimeError("private database detail")
    )
    monkeypatch.setattr(nodes, "create_watchlist_service", lambda: service)

    with caplog.at_level("ERROR", logger="src.agent.nodes"):
        result = nodes.manage_watchlist({"watchlist_action": "list"})

    assert result["final_answer"] == "团队关注清单操作暂时失败，请稍后重试。"
    assert "private database detail" not in result["final_answer"]
    assert "团队关注清单操作失败，action=list" in caplog.text
    assert "RuntimeError: private database detail" in caplog.text
