from datetime import datetime, timezone

from sqlalchemy import create_engine

from src.dmf_history.models import Base
from src.dmf_watchlist.repository import DMFWatchlistRepository
from src.dmf_watchlist.service import DMFWatchlistService
from src.schemas.dmf import (
    DMFCollectionStatus,
    DMFHistoryResult,
    DMFQuery,
    DMFQueryResult,
    DMFRecord,
    DMFSearchResult,
    DMFSingleQuery,
)
from src.schemas.watchlist import WatchlistEventType, WatchlistRunStatus


class SearchSequence:
    def __init__(self, results):
        self.results = iter(results)

    def __call__(self, **kwargs):
        return next(self.results).model_dump()


def search_result(records, snapshot_id, status=DMFCollectionStatus.SUCCESS_NONEMPTY):
    query = DMFSingleQuery(dmf_no="DMF-001")
    item = DMFQueryResult(
        success=status in {DMFCollectionStatus.SUCCESS_NONEMPTY, DMFCollectionStatus.SUCCESS_EMPTY},
        message="ok",
        query=query,
        collection_status=status,
        records=records,
        history=DMFHistoryResult(
            monitor_run_id=f"run-{snapshot_id}",
            snapshot_id=snapshot_id,
            history_status=(
                "empty_observation_recorded"
                if status == DMFCollectionStatus.SUCCESS_EMPTY
                else "completed"
            ),
        ),
    )
    return DMFSearchResult(
        success=item.success,
        message="ok",
        query=DMFQuery(dmf_no="DMF-001"),
        query_count=1,
        success_count=1 if item.success else 0,
        results=[item],
    )


def make_service(tmp_path, results):
    engine = create_engine(f"sqlite:///{tmp_path / 'watchlist-service.db'}")
    Base.metadata.create_all(engine)
    repository = DMFWatchlistRepository(engine)
    service = DMFWatchlistService(
        repository,
        object(),
        search=SearchSequence(results),
        now=lambda: datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    return repository, service


def test_first_present_is_baseline_then_field_change_is_event(tmp_path) -> None:
    repository, service = make_service(
        tmp_path,
        [
            search_result([DMFRecord(dmf_no="DMF-001", applicant_name="A")], "snap-1"),
            search_result([DMFRecord(dmf_no="dmf-001", applicant_name="B")], "snap-2"),
        ],
    )
    watchlist = repository.create_or_restore("DMF-001", 24)

    first = service.run_manual(watchlist.id, idempotency_key="manual:1")
    second = service.run_manual(watchlist.id, idempotency_key="manual:2")

    assert first.status == WatchlistRunStatus.NO_CHANGE.value
    assert second.status == WatchlistRunStatus.CHANGED.value
    events = repository.list_events(watchlist.id)
    assert [event.event_type for event in events] == [WatchlistEventType.FIELD_CHANGED.value]


def test_manual_empty_observations_confirm_absence_on_second_run(tmp_path) -> None:
    repository, service = make_service(
        tmp_path,
        [
            search_result([], "empty-1", DMFCollectionStatus.SUCCESS_EMPTY),
            search_result([], "empty-2", DMFCollectionStatus.SUCCESS_EMPTY),
        ],
    )
    watchlist = repository.create_or_restore("DMF-001", 24)

    service.run_manual(watchlist.id, idempotency_key="manual:1")
    second = service.run_manual(watchlist.id, idempotency_key="manual:2")

    assert second.status == WatchlistRunStatus.CHANGED.value
    current = repository.get(watchlist.id)
    assert current.consecutive_absent_count == 2
    assert current.absence_alerted is True
    assert repository.list_events(watchlist.id)[0].event_type == "absent_confirmed"


def test_repeated_manual_idempotency_key_does_not_count_twice(tmp_path) -> None:
    repository, service = make_service(
        tmp_path,
        [search_result([], "empty-1", DMFCollectionStatus.SUCCESS_EMPTY)],
    )
    watchlist = repository.create_or_restore("DMF-001", 24)

    first = service.run_manual(watchlist.id, idempotency_key="manual:same")
    repeated = service.run_manual(watchlist.id, idempotency_key="manual:same")

    assert repeated.id == first.id
    assert repository.get(watchlist.id).consecutive_absent_count == 1
    assert len(repository.list_runs(watchlist.id)) == 1


def test_expired_valid_date_alerts_once_per_expiration_episode(tmp_path) -> None:
    expired = DMFRecord(dmf_no="DMF-001", valid_date="115/9/2")
    future = DMFRecord(dmf_no="DMF-001", valid_date="2027-09-03")
    repository, service = make_service(
        tmp_path,
        [
            search_result([expired], "snap-1"),
            search_result([expired], "snap-2"),
            search_result([future], "snap-3"),
            search_result([expired], "snap-4"),
        ],
    )
    watchlist = repository.create_or_restore("DMF-001", 24)

    for index in range(4):
        service.run_manual(watchlist.id, idempotency_key=f"manual:expiry:{index}")

    expiration_events = [
        event
        for event in repository.list_events(watchlist.id)
        if event.event_type == WatchlistEventType.VALID_DATE_EXPIRED.value
    ]
    assert len(expiration_events) == 2
    assert repository.get(watchlist.id).expiration_alerted_valid_date == "2026-09-02"
