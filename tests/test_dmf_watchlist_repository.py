from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine

from src.dmf_history.models import Base
from src.dmf_watchlist.models import DMFWatchlistEvent
from src.dmf_watchlist.repository import DMFWatchlistRepository
from src.schemas.watchlist import (
    WatchlistEventStatus,
    WatchlistEventType,
    WatchlistObservation,
    WatchlistRunStatus,
    WatchlistRunTrigger,
    WatchlistStatus,
)


def make_repository(tmp_path) -> DMFWatchlistRepository:
    engine = create_engine(f"sqlite:///{tmp_path / 'watchlist.db'}")
    Base.metadata.create_all(engine)
    return DMFWatchlistRepository(engine)


def absent_observation(count: int, alerted: bool, event=False) -> WatchlistObservation:
    return WatchlistObservation(
        run_status=WatchlistRunStatus.CHANGED if event else WatchlistRunStatus.NO_CHANGE,
        consecutive_absent_count=count,
        absence_alerted=alerted,
        event_types=[WatchlistEventType.ABSENT_CONFIRMED] if event else [],
    )


def test_create_pause_resume_delete_and_restore(tmp_path) -> None:
    repository = make_repository(tmp_path)
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    watchlist = repository.create_or_restore(" DMF-001 ", 24, now=now)

    paused = repository.update(watchlist.id, status=WatchlistStatus.PAUSED, now=now)
    resumed = repository.update(paused.id, status=WatchlistStatus.ACTIVE, now=now)
    repository.soft_delete(resumed.id, now=now)
    restored = repository.create_or_restore("dmf-001", 48, now=now)

    assert restored.id == watchlist.id
    assert restored.status == WatchlistStatus.ACTIVE.value
    assert restored.interval_hours == 48


def test_due_claim_is_exclusive(tmp_path) -> None:
    repository = make_repository(tmp_path)
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    repository.create_or_restore("DMF-001", 24, now=now)

    first = repository.claim_due(now=now)
    second = repository.claim_due(now=now)

    assert len(first) == 1
    assert second == []


def test_manual_runs_count_absence_and_ack_is_idempotent(tmp_path) -> None:
    repository = make_repository(tmp_path)
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    watchlist = repository.create_or_restore("DMF-001", 24, now=now)

    first_claim = repository.claim_manual(watchlist.id, now=now)
    first_run = repository.start_run(
        first_claim, WatchlistRunTrigger.MANUAL, "manual:first", now=now
    )
    repository.finalize_run(
        claim=first_claim,
        run_id=first_run.id,
        observation=absent_observation(1, False),
        monitor_run_id=None,
        snapshot_id=None,
        previous_record=None,
        current_record=None,
        now=now,
    )

    later = now + timedelta(minutes=1)
    second_claim = repository.claim_manual(watchlist.id, now=later)
    second_run = repository.start_run(
        second_claim, WatchlistRunTrigger.MANUAL, "manual:second", now=later
    )
    repository.finalize_run(
        claim=second_claim,
        run_id=second_run.id,
        observation=absent_observation(2, True, event=True),
        monitor_run_id=None,
        snapshot_id=None,
        previous_record=None,
        current_record=None,
        now=later,
    )

    events = repository.list_events(watchlist.id)
    assert len(events) == 1
    assert events[0].event_type == WatchlistEventType.ABSENT_CONFIRMED.value
    first_ack = repository.acknowledge_event(watchlist.id, events[0].id, now=later)
    second_ack = repository.acknowledge_event(
        watchlist.id, events[0].id, now=later + timedelta(minutes=1)
    )
    assert first_ack.status == WatchlistEventStatus.ACKNOWLEDGED.value
    assert second_ack.acknowledged_at is not None
    assert second_ack.acknowledged_at.replace(tzinfo=timezone.utc) == later

    with repository.session_factory() as session:
        assert session.get(DMFWatchlistEvent, events[0].id) is not None
