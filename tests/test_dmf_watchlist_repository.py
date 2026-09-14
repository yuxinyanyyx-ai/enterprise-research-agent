from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from src.dmf_history.models import Base
from src.dmf_watchlist.models import DMFWatchlistEvent
from src.dmf_watchlist.repository import DMFWatchlistRepository
from src.schemas.watchlist import (
    WatchlistEventStatus,
    WatchlistEventType,
    WatchlistNotificationMode,
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


def test_notification_config_creation_and_validation(tmp_path) -> None:
    repository = make_repository(tmp_path)
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="开启通知时必须指定至少一个接收邮箱"):
        repository.create_or_restore(
            "DMF-001",
            24,
            notification_enabled=True,
            notification_emails=[],
            now=now,
        )

    valid = repository.create_or_restore(
        "DMF-001",
        24,
        notification_enabled=True,
        notification_emails=["test@example.com"],
        notification_mode=WatchlistNotificationMode.WEEKLY_DIGEST,
        notification_fields={"notification_enabled", "notification_emails", "notification_mode"},
        now=now,
    )
    assert valid.notification_enabled is True
    assert valid.notification_emails == ["test@example.com"]
    assert valid.notification_mode == WatchlistNotificationMode.WEEKLY_DIGEST.value


def test_notification_config_update_and_validation(tmp_path) -> None:
    repository = make_repository(tmp_path)
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    watchlist = repository.create_or_restore(
        "DMF-001",
        24,
        notification_enabled=True,
        notification_emails=["test@example.com"],
        notification_fields={"notification_enabled", "notification_emails"},
        now=now,
    )

    # 关闭通知，原邮箱和模式保留
    updated = repository.update(
        watchlist.id,
        notification_enabled=False,
        notification_fields={"notification_enabled"},
        now=now,
    )
    assert updated.notification_enabled is False
    assert updated.notification_emails == ["test@example.com"]

    # 将邮箱清空，因为 notification_enabled 为 False，允许成功保存
    updated_emails = repository.update(
        watchlist.id,
        notification_emails=[],
        notification_fields={"notification_emails"},
        now=now,
    )
    assert updated_emails.notification_emails == []

    # 无邮箱时重新开启通知失败
    with pytest.raises(ValueError, match="开启通知时必须指定至少一个接收邮箱"):
        repository.update(
            watchlist.id,
            notification_enabled=True,
            notification_fields={"notification_enabled"},
            now=now,
        )


def test_restore_deleted_watchlist_notification_policy(tmp_path) -> None:
    repository = make_repository(tmp_path)
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)

    # 1. 开启通知并添加关注项，后软删除
    original = repository.create_or_restore(
        "DMF-001",
        24,
        notification_enabled=True,
        notification_emails=["team@example.com"],
        notification_mode=WatchlistNotificationMode.WEEKLY_DIGEST,
        notification_fields={"notification_enabled", "notification_emails", "notification_mode"},
        now=now,
    )
    repository.soft_delete(original.id, now=now)

    # 2. 恢复不传通知字段：配置保留原邮箱和模式，但 notification_enabled 强制为 False
    restored_default = repository.create_or_restore("dmf-001", 24, now=now)
    assert restored_default.id == original.id
    assert restored_default.notification_enabled is False
    assert restored_default.notification_emails == ["team@example.com"]
    assert restored_default.notification_mode == WatchlistNotificationMode.WEEKLY_DIGEST.value

    # 3. 再次软删除并用新通知配置恢复
    repository.soft_delete(restored_default.id, now=now)
    restored_new = repository.create_or_restore(
        "DMF-001",
        48,
        notification_enabled=True,
        notification_emails=["new@example.com"],
        notification_mode=WatchlistNotificationMode.IMMEDIATE,
        notification_fields={"notification_enabled", "notification_emails", "notification_mode"},
        now=now,
    )
    assert restored_new.notification_enabled is True
    assert restored_new.notification_emails == ["new@example.com"]
    assert restored_new.notification_mode == WatchlistNotificationMode.IMMEDIATE.value
