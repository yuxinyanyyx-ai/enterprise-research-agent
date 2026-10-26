from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy import create_engine

from src.dmf_history.models import (
    Base,
    DMFChangeEventModel,
    DMFSnapshot,
)
from src.dmf_history.repository import DMFHistoryRepository
from src.dmf_watchlist import repository as watchlist_repository_module
from src.dmf_watchlist.models import (
    DMFNotificationDelivery,
    DMFWatchlist,
    DMFWatchlistEvent,
    DMFWatchlistRun,
)
from src.dmf_watchlist.notification_dispatcher import NotificationDispatcher
from src.dmf_watchlist.repository import DMFWatchlistRepository
from src.dmf_watchlist.service import DMFWatchlistService
from src.dmf_watchlist.worker import run_due_once
from src.schemas.dmf import (
    DMFCollectionStatus,
    DMFQuery,
    DMFQueryResult,
    DMFRecord,
    DMFSearchResult,
    DMFSingleQuery,
)
from src.schemas.watchlist import (
    WatchlistEventStatus,
    WatchlistEventType,
    WatchlistRunStatus,
)
from tests.test_email_service import email_settings


class SnapshotSearch:
    def __init__(self, history_repository, records_by_call):
        self.history_repository = history_repository
        self.records_by_call = iter(records_by_call)
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        records = next(self.records_by_call)
        queried_at = datetime.now(timezone.utc)
        query = DMFSingleQuery(dmf_no="DMF-001")
        item = DMFQueryResult(
            success=True,
            message="ok",
            query=query,
            collection_status=DMFCollectionStatus.SUCCESS_NONEMPTY,
            total=len(records),
            total_pages=1,
            successful_pages=1,
            started_at=queried_at,
            ended_at=queried_at,
            queried_at=queried_at,
            records=records,
        )
        item.history = self.history_repository.record_query(
            item, queried_at=queried_at
        )
        return DMFSearchResult(
            success=True,
            message="ok",
            query=DMFQuery(dmf_no="DMF-001"),
            query_count=1,
            success_count=1,
            total_records=len(records),
            results=[item],
        ).model_dump()


class MemorySender:
    def __init__(self):
        self.calls = []

    def send(self, recipient, payload, message_id):
        self.calls.append((recipient, payload, message_id))


def make_monitor(tmp_path, records_by_call):
    engine = create_engine(f"sqlite:///{tmp_path / 'dmf-monitor.db'}")
    history_repository = DMFHistoryRepository(
        f"sqlite:///{tmp_path / 'dmf-monitor.db'}", engine=engine
    )
    history_repository.initialize_schema()
    watchlist_repository = DMFWatchlistRepository(engine)
    search = SnapshotSearch(history_repository, records_by_call)
    service = DMFWatchlistService(
        watchlist_repository,
        history_repository,
        search=search,
    )
    return engine, history_repository, watchlist_repository, service, search


def test_query_snapshot_change_notification_and_acknowledgement_e2e(tmp_path):
    original = DMFRecord(
        dmf_no="DMF-001",
        applicant_name="Company A",
        ingredient="Ibuprofen",
        valid_date="2028-01-01",
    )
    changed = DMFRecord(
        dmf_no="DMF-001",
        applicant_name="Company B",
        ingredient="Ibuprofen",
        valid_date="2028-01-01",
    )
    added = DMFRecord(dmf_no="DMF-002", applicant_name="Company C")
    engine, history, repository, service, _ = make_monitor(
        tmp_path, [[original], [changed, added]]
    )
    watchlist = service.add(
        "DMF-001",
        notification_enabled=True,
        notification_emails=["ops@example.com"],
    )

    first = service.run_manual(watchlist.id, idempotency_key="e2e:first")
    second = service.run_manual(watchlist.id, idempotency_key="e2e:second")

    assert first.status == WatchlistRunStatus.NO_CHANGE.value
    assert second.status == WatchlistRunStatus.CHANGED.value
    events = repository.list_events(watchlist.id)
    assert len(events) == 1
    assert events[0].event_type == WatchlistEventType.FIELD_CHANGED.value
    assert events[0].status == WatchlistEventStatus.UNREAD.value

    with history.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(DMFSnapshot)) == 2
        assert (
            session.scalar(select(func.count()).select_from(DMFChangeEventModel))
            == 2
        )

    sender = MemorySender()
    dispatcher = NotificationDispatcher(repository, email_settings(), sender)
    assert dispatcher.run_once(limit=10) == 1
    assert len(sender.calls) == 1
    with repository.session_factory() as session:
        delivery = session.scalar(select(DMFNotificationDelivery))
        assert delivery.status == "sent"
        assert delivery.payload["events"][0]["event_type"] == "field_changed"

    acknowledged = service.acknowledge_event(
        events[0].id, watchlist_id=watchlist.id
    )
    assert acknowledged.status == WatchlistEventStatus.ACKNOWLEDGED.value
    engine.dispose()


def test_worker_retry_after_outbox_failure_resumes_same_run_without_duplicate_alert(
    tmp_path, monkeypatch
):
    original = DMFRecord(dmf_no="DMF-001", applicant_name="Company A")
    changed = DMFRecord(dmf_no="DMF-001", applicant_name="Company B")
    engine, history, repository, service, search = make_monitor(
        tmp_path, [[original], [changed], [changed]]
    )
    watchlist = service.add(
        "DMF-001",
        notification_enabled=True,
        notification_emails=["ops@example.com"],
    )
    service.run_manual(watchlist.id, idempotency_key="worker:baseline")
    scheduled_for = datetime.now(timezone.utc)
    with repository.session_factory.begin() as session:
        session.execute(
            update(DMFWatchlist)
            .where(DMFWatchlist.id == watchlist.id)
            .values(next_run_at=scheduled_for)
        )

    original_enqueue = watchlist_repository_module.enqueue_delivery
    fail_once = [True]

    def fail_first_enqueue(*args, **kwargs):
        if fail_once[0]:
            fail_once[0] = False
            raise RuntimeError("injected outbox failure")
        return original_enqueue(*args, **kwargs)

    monkeypatch.setattr(
        watchlist_repository_module, "enqueue_delivery", fail_first_enqueue
    )

    assert run_due_once(repository, service, limit=1) == 1
    interrupted = repository.list_runs(watchlist.id)
    assert len(interrupted) == 2
    assert interrupted[0].status == WatchlistRunStatus.RUNNING.value
    assert repository.get(watchlist.id).lock_token is None

    restarted_service = DMFWatchlistService(
        repository,
        history,
        search=search,
    )
    assert run_due_once(repository, restarted_service, limit=1) == 1

    runs = repository.list_runs(watchlist.id)
    events = repository.list_events(watchlist.id)
    with repository.session_factory() as session:
        deliveries = list(session.scalars(select(DMFNotificationDelivery)))
        history_changes = list(session.scalars(select(DMFChangeEventModel)))

    assert search.calls == 3
    assert len(runs) == 2
    assert runs[0].id == interrupted[0].id
    assert runs[0].status == WatchlistRunStatus.CHANGED.value
    assert len(events) == 1
    assert events[0].event_type == WatchlistEventType.FIELD_CHANGED.value
    assert len(deliveries) == 1
    assert len(history_changes) == 1
    assert repository.get(watchlist.id).baseline_payload == changed.model_dump()
    engine.dispose()
