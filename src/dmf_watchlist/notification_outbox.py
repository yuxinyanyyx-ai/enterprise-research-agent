"""Transactional notification snapshots; no network operations."""

from datetime import datetime, timezone
from uuid import uuid4

from .models import DMFNotificationDelivery


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def event_payload(event) -> dict:
    return {
        "id": event.id, "created_at": utc(event.created_at).isoformat(),
        "event_type": event.event_type,
        "before": event.before_payload, "after": event.after_payload,
    }


def enqueue_delivery(session, watchlist, payload: dict, key: str, now: datetime) -> None:
    for recipient in dict.fromkeys(watchlist.notification_emails):
        session.add(DMFNotificationDelivery(
            id=str(uuid4()), watchlist_id=watchlist.id,
            generation=watchlist.notification_generation, recipient=recipient,
            mode=watchlist.notification_mode, payload=payload,
            dedupe_key=f"{watchlist.id}:{watchlist.notification_generation}:{key}:{recipient}",
            status="pending", attempts=0, next_attempt_at=now, created_at=now,
        ))


def invalidate_notifications(watchlist, now: datetime) -> None:
    watchlist.notification_generation += 1
    watchlist.notification_since = now
    watchlist.last_digest_enqueued_at = None