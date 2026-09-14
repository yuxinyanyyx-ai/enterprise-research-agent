"""Durable weekly scheduling and leased, per-recipient delivery."""

from datetime import datetime, timedelta, timezone
import logging
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select, update

from src.notifications.email_service import EmailService
from src.settings import Settings
from .models import DMFNotificationDelivery, DMFWatchlist, DMFWatchlistEvent
from .notification_outbox import enqueue_delivery, event_payload, utc
from .repository import DMFWatchlistRepository


logger = logging.getLogger(__name__)


def next_weekly_boundary(after: datetime, settings: Settings) -> datetime:
    local = utc(after).astimezone(ZoneInfo(settings.notification_timezone))
    boundary = local.replace(hour=settings.notification_weekly_digest_hour, minute=0, second=0, microsecond=0)
    boundary += timedelta(days=(settings.notification_weekly_digest_day - local.weekday()) % 7)
    if boundary <= local:
        boundary += timedelta(days=7)
    return boundary.astimezone(timezone.utc)


class NotificationDispatcher:
    def __init__(self, repository: DMFWatchlistRepository, settings: Settings, sender=None, *, clock=None) -> None:
        self.repository = repository
        self.settings = settings
        self.sender = sender or EmailService(settings)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def enqueue_weekly_digests(self, now: datetime) -> int:
        if not self.settings.notification_enabled:
            return 0
        now = utc(now)
        count = 0
        for candidate in self.repository.list():
            if candidate.status != "active" or not candidate.notification_enabled:
                continue
            if candidate.notification_mode != "weekly_digest" or not candidate.notification_emails:
                continue
            with self.repository.session_factory.begin() as session:
                watchlist = session.get(DMFWatchlist, candidate.id)
                if watchlist is None:
                    continue
                if watchlist.notification_since is None:
                    session.execute(update(DMFWatchlist).where(
                        DMFWatchlist.id == watchlist.id,
                        DMFWatchlist.notification_since.is_(None),
                    ).values(notification_since=now))
                    continue
                start = utc(watchlist.last_digest_enqueued_at or watchlist.notification_since)
                end = next_weekly_boundary(start, self.settings)
                if end > now:
                    continue
                previous = watchlist.last_digest_enqueued_at
                claimed = session.execute(update(DMFWatchlist).where(
                    DMFWatchlist.id == watchlist.id,
                    DMFWatchlist.notification_generation == watchlist.notification_generation,
                    DMFWatchlist.status == "active",
                    DMFWatchlist.notification_enabled.is_(True),
                    DMFWatchlist.notification_mode == "weekly_digest",
                    DMFWatchlist.last_digest_enqueued_at == previous,
                ).values(last_digest_enqueued_at=end))
                if claimed.rowcount != 1:
                    continue
                events = session.scalars(select(DMFWatchlistEvent).where(
                    DMFWatchlistEvent.watchlist_id == watchlist.id,
                    DMFWatchlistEvent.notification_generation == watchlist.notification_generation,
                    DMFWatchlistEvent.created_at >= start,
                    DMFWatchlistEvent.created_at < end,
                ).order_by(DMFWatchlistEvent.created_at, DMFWatchlistEvent.id))
                enqueue_delivery(session, watchlist, {
                    "dmf_no": watchlist.dmf_no, "mode": "weekly_digest",
                    "period_start": start.isoformat(), "period_end": end.isoformat(),
                    "events": [event_payload(event) for event in events],
                    "last_run_at": utc(watchlist.last_run_at).isoformat() if watchlist.last_run_at else None,
                    "failure_count": watchlist.failure_count,
                }, f"weekly:{end.isoformat()}", now)
                count += 1
        return count

    def claim_delivery(self, now: datetime):
        now = utc(now)
        due = or_(
            (DMFNotificationDelivery.status == "pending") & (DMFNotificationDelivery.next_attempt_at <= now),
            (DMFNotificationDelivery.status == "sending") & (DMFNotificationDelivery.locked_until <= now),
        )
        with self.repository.session_factory.begin() as session:
            delivery_id = session.scalar(select(DMFNotificationDelivery.id).where(due).order_by(
                DMFNotificationDelivery.next_attempt_at, DMFNotificationDelivery.id
            ).limit(1))
            if delivery_id is None:
                return None
            token = str(uuid4())
            claimed = session.execute(update(DMFNotificationDelivery).where(
                DMFNotificationDelivery.id == delivery_id, due,
            ).values(
                status="sending", lock_token=token,
                locked_until=now + timedelta(seconds=max(900, self.settings.smtp_timeout_seconds * 10)),
            ))
            if claimed.rowcount != 1:
                return None
            delivery = session.get(DMFNotificationDelivery, delivery_id)
            watchlist = session.get(DMFWatchlist, delivery.watchlist_id)
            if (
                watchlist is None or watchlist.status != "active" or not watchlist.notification_enabled
                or watchlist.notification_generation != delivery.generation
                or watchlist.notification_mode != delivery.mode
                or delivery.recipient not in watchlist.notification_emails
            ):
                delivery.status = "cancelled"
            elif delivery.attempts >= self.settings.notification_max_retries + 1:
                delivery.status = "failed"
                delivery.error_code = "DELIVERY_LEASE_EXPIRED"
            else:
                delivery.attempts += 1
                return delivery
            delivery.locked_until = None
            delivery.lock_token = None
            return delivery

    def finish_delivery(self, delivery, now: datetime, error: Exception | None = None) -> None:
        failed = error is not None
        terminal = delivery.attempts >= self.settings.notification_max_retries + 1
        values = {
            "status": ("failed" if terminal else "pending") if failed else "sent",
            "sent_at": None if failed else now,
            "error_code": type(error).__name__[:64] if failed else None,
            "lock_token": None, "locked_until": None,
        }
        if failed:
            values["next_attempt_at"] = now + timedelta(
                seconds=min(86400, self.settings.notification_retry_seconds * 2 ** min(delivery.attempts - 1, 16))
            )
        with self.repository.session_factory.begin() as session:
            session.execute(update(DMFNotificationDelivery).where(
                DMFNotificationDelivery.id == delivery.id,
                DMFNotificationDelivery.lock_token == delivery.lock_token,
                DMFNotificationDelivery.status == "sending",
            ).values(**values))

    def dispatch_pending(self, now: datetime | None = None, *, limit: int = 20) -> int:
        if not self.settings.notification_enabled:
            return 0
        sent = 0
        for _attempt in range(limit):
            delivery = self.claim_delivery(utc(now or self.clock()))
            if delivery is None:
                break
            if delivery.status != "sending":
                continue
            error = None
            try:
                self.sender.send(delivery.recipient, delivery.payload, f"<{delivery.id}@dmf.local>")
            except Exception as exc:
                error = exc
                logger.warning("DMF email failed: delivery_id=%s error_type=%s", delivery.id, type(exc).__name__)
            self.finish_delivery(delivery, utc(now or self.clock()), error)
            if error is None:
                sent += 1
        return sent

    def run_once(self, *, now: datetime | None = None, limit: int = 20) -> int:
        if not self.settings.notification_enabled:
            return 0
        try:
            self.enqueue_weekly_digests(utc(now or self.clock()))
        except Exception as exc:
            logger.error("DMF digest scheduling failed: %s", type(exc).__name__)
        return self.dispatch_pending(now, limit=limit)