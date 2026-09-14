"""Transactional persistence and locking for DMF watchlists."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import Engine, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.dmf_history.comparison import normalize_text
from src.schemas.dmf import DMFRecord
from src.schemas.watchlist import (
    WatchlistEventStatus,
    WatchlistEventType,
    WatchlistObservation,
    WatchlistRunStatus,
    WatchlistRunTrigger,
    WatchlistNotificationMode,
    WatchlistStatus,
)

from .models import DMFWatchlist, DMFWatchlistEvent, DMFWatchlistRun
from .notification_outbox import enqueue_delivery, event_payload, invalidate_notifications


@dataclass(frozen=True, slots=True)
class WatchlistClaim:
    watchlist_id: str
    lock_token: str
    dmf_no: str
    scheduled_for: datetime


class WatchlistNotFoundError(LookupError):
    pass


class WatchlistConflictError(RuntimeError):
    pass


class DMFWatchlistRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.session_factory = sessionmaker(engine, expire_on_commit=False)

    def create_or_restore(
        self,
        dmf_no: str,
        interval_hours: int,
        *,
        notification_enabled: bool = False,
        notification_emails: list[str] | None = None,
        notification_mode: WatchlistNotificationMode = WatchlistNotificationMode.IMMEDIATE,
        notification_fields: set[str] | None = None,
        now: datetime | None = None,
    ) -> DMFWatchlist:
        observed_at = _utc(now)
        normalized = normalize_text(dmf_no)
        if not normalized:
            raise ValueError("DMF 编号不能为空")
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(DMFWatchlist).where(DMFWatchlist.normalized_dmf_no == normalized)
            )
            if existing is not None:
                if existing.status != WatchlistStatus.DELETED.value:
                    raise WatchlistConflictError("该 DMF 已在关注清单中")
                existing.dmf_no = dmf_no.strip()
                existing.status = WatchlistStatus.ACTIVE.value
                existing.interval_hours = interval_hours
                has_notification_fields = bool(notification_fields)
                if has_notification_fields:
                    existing.notification_enabled = notification_enabled
                    if "notification_emails" in notification_fields and notification_emails is not None:
                        existing.notification_emails = notification_emails
                    if "notification_mode" in notification_fields:
                        existing.notification_mode = notification_mode.value
                else:
                    existing.notification_enabled = False
                _validate_notification_config(
                    existing.notification_enabled, existing.notification_emails
                )
                existing.next_run_at = observed_at
                existing.updated_at = observed_at
                existing.deleted_at = None
                existing.paused_at = None
                invalidate_notifications(existing, observed_at)
                return existing
            watchlist = DMFWatchlist(
                id=str(uuid4()),
                dmf_no=dmf_no.strip(),
                normalized_dmf_no=normalized,
                status=WatchlistStatus.ACTIVE.value,
                interval_hours=interval_hours,
                next_run_at=observed_at,
                consecutive_absent_count=0,
                absence_alerted=False,
                failure_count=0,
                notification_enabled=notification_enabled,
                notification_emails=notification_emails or [],
                notification_mode=notification_mode.value,
                notification_since=observed_at,
                created_at=observed_at,
                updated_at=observed_at,
            )
            session.add(watchlist)
            _validate_notification_config(
                watchlist.notification_enabled, watchlist.notification_emails
            )
            return watchlist

    def get(self, watchlist_id: str, *, include_deleted: bool = False) -> DMFWatchlist:
        with self.session_factory() as session:
            watchlist = session.get(DMFWatchlist, watchlist_id)
            if watchlist is None or (
                not include_deleted and watchlist.status == WatchlistStatus.DELETED.value
            ):
                raise WatchlistNotFoundError(watchlist_id)
            session.expunge(watchlist)
            return watchlist

    def list(self, *, include_deleted: bool = False) -> list[DMFWatchlist]:
        with self.session_factory() as session:
            statement = select(DMFWatchlist).order_by(DMFWatchlist.created_at.desc())
            if not include_deleted:
                statement = statement.where(DMFWatchlist.status != WatchlistStatus.DELETED.value)
            rows = list(session.scalars(statement))
            for row in rows:
                session.expunge(row)
            return rows

    def update(
        self,
        watchlist_id: str,
        *,
        status: WatchlistStatus | None = None,
        interval_hours: int | None = None,
        notification_enabled: bool | None = None,
        notification_emails: list[str] | None = None,
        notification_mode: WatchlistNotificationMode | None = None,
        notification_fields: set[str] | None = None,
        now: datetime | None = None,
    ) -> DMFWatchlist:
        observed_at = _utc(now)
        with self.session_factory.begin() as session:
            watchlist = session.get(DMFWatchlist, watchlist_id)
            if watchlist is None or watchlist.status == WatchlistStatus.DELETED.value:
                raise WatchlistNotFoundError(watchlist_id)
            if status == WatchlistStatus.DELETED:
                raise ValueError("请使用删除操作")
            previous_notification = (
                watchlist.notification_enabled, watchlist.notification_emails,
                watchlist.notification_mode, watchlist.status,
            )
            if interval_hours is not None:
                watchlist.interval_hours = interval_hours
            fields = notification_fields or set()
            if "notification_enabled" in fields:
                watchlist.notification_enabled = bool(notification_enabled)
            if "notification_emails" in fields:
                watchlist.notification_emails = notification_emails or []
            if "notification_mode" in fields and notification_mode is not None:
                watchlist.notification_mode = notification_mode.value
            _validate_notification_config(
                watchlist.notification_enabled, watchlist.notification_emails
            )
            if status is not None:
                watchlist.status = status.value
                watchlist.paused_at = observed_at if status == WatchlistStatus.PAUSED else None
                if status == WatchlistStatus.ACTIVE:
                    watchlist.next_run_at = observed_at
            if previous_notification != (
                watchlist.notification_enabled, watchlist.notification_emails,
                watchlist.notification_mode, watchlist.status,
            ):
                invalidate_notifications(watchlist, observed_at)
            watchlist.updated_at = observed_at
            return watchlist

    def soft_delete(self, watchlist_id: str, *, now: datetime | None = None) -> None:
        observed_at = _utc(now)
        with self.session_factory.begin() as session:
            watchlist = session.get(DMFWatchlist, watchlist_id)
            if watchlist is None or watchlist.status == WatchlistStatus.DELETED.value:
                raise WatchlistNotFoundError(watchlist_id)
            watchlist.status = WatchlistStatus.DELETED.value
            invalidate_notifications(watchlist, observed_at)
            watchlist.deleted_at = observed_at
            watchlist.updated_at = observed_at
            watchlist.next_run_at = None
            watchlist.locked_until = None
            watchlist.lock_token = None

    def claim_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 20,
        lock_seconds: int = 900,
    ) -> list[WatchlistClaim]:
        observed_at = _utc(now)
        claims: list[WatchlistClaim] = []
        with self.session_factory.begin() as session:
            ids = list(
                session.scalars(
                    select(DMFWatchlist.id)
                    .where(
                        DMFWatchlist.status == WatchlistStatus.ACTIVE.value,
                        DMFWatchlist.next_run_at <= observed_at,
                        or_(
                            DMFWatchlist.locked_until.is_(None),
                            DMFWatchlist.locked_until < observed_at,
                        ),
                    )
                    .order_by(DMFWatchlist.next_run_at)
                    .limit(limit)
                )
            )
            for watchlist_id in ids:
                token = str(uuid4())
                claimed = session.execute(
                    update(DMFWatchlist)
                    .where(
                        DMFWatchlist.id == watchlist_id,
                        DMFWatchlist.status == WatchlistStatus.ACTIVE.value,
                        or_(
                            DMFWatchlist.locked_until.is_(None),
                            DMFWatchlist.locked_until < observed_at,
                        ),
                    )
                    .values(
                        locked_until=observed_at + timedelta(seconds=lock_seconds),
                        lock_token=token,
                    )
                )
                if claimed.rowcount == 1:
                    watchlist = session.get(DMFWatchlist, watchlist_id)
                    if watchlist is not None:
                        claims.append(
                            WatchlistClaim(
                                watchlist_id=watchlist.id,
                                lock_token=token,
                                dmf_no=watchlist.dmf_no,
                                scheduled_for=watchlist.next_run_at or observed_at,
                            )
                        )
        return claims

    def claim_manual(
        self, watchlist_id: str, *, now: datetime | None = None, lock_seconds: int = 900
    ) -> WatchlistClaim:
        observed_at = _utc(now)
        token = str(uuid4())
        with self.session_factory.begin() as session:
            claimed = session.execute(
                update(DMFWatchlist)
                .where(
                    DMFWatchlist.id == watchlist_id,
                    DMFWatchlist.status == WatchlistStatus.ACTIVE.value,
                    or_(
                        DMFWatchlist.locked_until.is_(None),
                        DMFWatchlist.locked_until < observed_at,
                    ),
                )
                .values(
                    locked_until=observed_at + timedelta(seconds=lock_seconds),
                    lock_token=token,
                )
            )
            if claimed.rowcount != 1:
                watchlist = session.get(DMFWatchlist, watchlist_id)
                if watchlist is None or watchlist.status == WatchlistStatus.DELETED.value:
                    raise WatchlistNotFoundError(watchlist_id)
                raise WatchlistConflictError("关注项未启用或正在执行")
            watchlist = session.get(DMFWatchlist, watchlist_id)
            if watchlist is None:
                raise WatchlistNotFoundError(watchlist_id)
            return WatchlistClaim(watchlist.id, token, watchlist.dmf_no, observed_at)

    def start_run(
        self,
        claim: WatchlistClaim,
        trigger: WatchlistRunTrigger,
        idempotency_key: str,
        *,
        now: datetime | None = None,
    ) -> DMFWatchlistRun:
        observed_at = _utc(now)
        try:
            with self.session_factory.begin() as session:
                watchlist = session.get(DMFWatchlist, claim.watchlist_id)
                if watchlist is None or watchlist.lock_token != claim.lock_token:
                    raise WatchlistConflictError("关注项执行锁已失效")
                run = DMFWatchlistRun(
                    id=str(uuid4()),
                    watchlist_id=claim.watchlist_id,
                    trigger=trigger.value,
                    status=WatchlistRunStatus.RUNNING.value,
                    scheduled_for=claim.scheduled_for,
                    idempotency_key=idempotency_key,
                    started_at=observed_at,
                    warnings=[],
                )
                session.add(run)
                return run
        except IntegrityError as exc:
            with self.session_factory() as session:
                existing = session.scalar(
                    select(DMFWatchlistRun).where(
                        DMFWatchlistRun.idempotency_key == idempotency_key
                    )
                )
                if existing is None or existing.watchlist_id != claim.watchlist_id:
                    raise WatchlistConflictError("该观察已执行") from exc
                session.expunge(existing)
                return existing

    def finalize_run(
        self,
        *,
        claim: WatchlistClaim,
        run_id: str,
        observation: WatchlistObservation,
        monitor_run_id: str | None,
        snapshot_id: str | None,
        previous_record: DMFRecord | None,
        current_record: DMFRecord | None,
        error_code: str | None = None,
        error_message: str | None = None,
        now: datetime | None = None,
    ) -> DMFWatchlistRun:
        observed_at = _utc(now)
        with self.session_factory.begin() as session:
            watchlist = session.get(DMFWatchlist, claim.watchlist_id)
            run = session.get(DMFWatchlistRun, run_id)
            if watchlist is None or run is None or watchlist.lock_token != claim.lock_token:
                raise WatchlistConflictError("关注项执行锁已失效")

            run.status = observation.run_status.value
            run.monitor_run_id = monitor_run_id
            run.snapshot_id = snapshot_id
            run.ended_at = observed_at
            run.error_code = error_code
            run.error_message = error_message
            run.warnings = observation.warnings

            watchlist.last_run_at = observed_at
            watchlist.consecutive_absent_count = observation.consecutive_absent_count
            watchlist.absence_alerted = observation.absence_alerted
            previous_expiration_alert = watchlist.expiration_alerted_valid_date
            watchlist.expiration_alerted_valid_date = observation.expiration_alerted_valid_date
            if (
                previous_expiration_alert is not None
                and observation.expiration_alerted_valid_date is None
            ):
                watchlist.expiration_alert_version += 1
            watchlist.failure_count = (
                watchlist.failure_count + 1
                if observation.run_status in {
                    WatchlistRunStatus.QUERY_FAILED,
                    WatchlistRunStatus.HISTORY_FAILED,
                }
                else 0
            )
            if current_record is not None:
                watchlist.baseline_payload = current_record.model_dump()
                watchlist.last_eligible_snapshot_id = snapshot_id
            watchlist.next_run_at = observed_at + timedelta(hours=watchlist.interval_hours)
            watchlist.locked_until = None
            watchlist.lock_token = None
            watchlist.updated_at = observed_at

            for event_type in observation.event_types:
                dedupe_key = (
                    f"{watchlist.id}:{event_type.value}:"
                    f"{watchlist.expiration_alert_version}:"
                    f"{observation.normalized_valid_date}"
                    if event_type == WatchlistEventType.VALID_DATE_EXPIRED
                    else f"{run.id}:{event_type.value}"
                )
                event = DMFWatchlistEvent(
                        id=str(uuid4()),
                        watchlist_id=watchlist.id,
                        run_id=run.id,
                        snapshot_id=snapshot_id,
                        event_type=event_type.value,
                        before_payload=previous_record.model_dump() if previous_record else None,
                        after_payload=current_record.model_dump() if current_record else None,
                        status=WatchlistEventStatus.UNREAD.value,
                        dedupe_key=dedupe_key,
                        created_at=observed_at,
                        notification_generation=(
                            watchlist.notification_generation
                            if watchlist.notification_enabled and watchlist.status == "active"
                            else -1
                        ),
                    )
                session.add(event)
                if (
                    event.notification_generation >= 0
                    and watchlist.notification_mode == "immediate"
                ):
                    enqueue_delivery(session, watchlist, {
                        "dmf_no": watchlist.dmf_no, "mode": "immediate",
                        "events": [event_payload(event)],
                    }, event.id, observed_at)
            return run

    def release_failed_claim(
        self, claim: WatchlistClaim, *, now: datetime | None = None
    ) -> None:
        observed_at = _utc(now)
        with self.session_factory.begin() as session:
            session.execute(
                update(DMFWatchlist)
                .where(
                    DMFWatchlist.id == claim.watchlist_id,
                    DMFWatchlist.lock_token == claim.lock_token,
                )
                .values(locked_until=None, lock_token=None, updated_at=observed_at)
            )

    def list_runs(self, watchlist_id: str) -> list[DMFWatchlistRun]:
        self.get(watchlist_id)
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(DMFWatchlistRun)
                    .where(DMFWatchlistRun.watchlist_id == watchlist_id)
                    .order_by(DMFWatchlistRun.started_at.desc())
                )
            )

    def list_events(self, watchlist_id: str) -> list[DMFWatchlistEvent]:
        self.get(watchlist_id)
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(DMFWatchlistEvent)
                    .where(DMFWatchlistEvent.watchlist_id == watchlist_id)
                    .order_by(DMFWatchlistEvent.created_at.desc())
                )
            )

    def acknowledge_event(
        self, watchlist_id: str, event_id: str, *, now: datetime | None = None
    ) -> DMFWatchlistEvent:
        observed_at = _utc(now)
        with self.session_factory.begin() as session:
            event = session.get(DMFWatchlistEvent, event_id)
            if event is None or event.watchlist_id != watchlist_id:
                raise WatchlistNotFoundError(event_id)
            if event.status != WatchlistEventStatus.ACKNOWLEDGED.value:
                event.status = WatchlistEventStatus.ACKNOWLEDGED.value
                event.acknowledged_at = observed_at
            return event


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    return result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)


def _validate_notification_config(enabled: bool, emails: list[str] | None) -> None:
    if enabled and not emails:
        raise ValueError("开启通知时必须指定至少一个接收邮箱")
