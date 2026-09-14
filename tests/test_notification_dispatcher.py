from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select

from src.dmf_watchlist.models import DMFNotificationDelivery
from src.schemas.watchlist import WatchlistNotificationMode
from tests.test_dmf_watchlist_repository import make_repository, absent_observation
from src.schemas.watchlist import WatchlistRunTrigger
from src.dmf_watchlist.notification_dispatcher import NotificationDispatcher, next_weekly_boundary
from tests.test_email_service import email_settings


NOW = datetime(2026, 9, 7, 0, tzinfo=timezone.utc)


def create_watch(repository, mode=WatchlistNotificationMode.IMMEDIATE, enabled=True):
    return repository.create_or_restore(
        "DMF-001", 24, notification_enabled=enabled,
        notification_emails=["one@example.com", "two@example.com"],
        notification_mode=mode, now=NOW,
    )


def add_event(repository, watchlist, now=NOW):
    claim = repository.claim_manual(watchlist.id, now=now)
    run = repository.start_run(claim, WatchlistRunTrigger.MANUAL, now.isoformat(), now=now)
    repository.finalize_run(
        claim=claim, run_id=run.id, observation=absent_observation(2, True, event=True),
        monitor_run_id=None, snapshot_id=None, previous_record=None, current_record=None, now=now,
    )


def deliveries(repository):
    with repository.session_factory() as session:
        return list(session.scalars(select(DMFNotificationDelivery).order_by(DMFNotificationDelivery.recipient)))


def test_event_commits_one_outbox_delivery_per_recipient(tmp_path):
    repository = make_repository(tmp_path)
    watchlist = create_watch(repository)
    add_event(repository, watchlist)
    rows = deliveries(repository)
    assert len(rows) == 2
    assert rows[0].payload["events"][0]["id"] == repository.list_events(watchlist.id)[0].id
    assert {row.recipient for row in rows} == {"one@example.com", "two@example.com"}
    repository.soft_delete(watchlist.id, now=NOW)
    restored = repository.create_or_restore("DMF-001", 24, now=NOW)
    assert restored.notification_generation != rows[0].generation


def test_disabled_events_are_not_enqueued(tmp_path):
    repository = make_repository(tmp_path)
    watchlist = create_watch(repository, enabled=False)
    add_event(repository, watchlist)
    assert deliveries(repository) == []
    assert repository.list_events(watchlist.id)[0].notification_generation == -1


class Sender:
    def __init__(self):
        self.calls = []
        self.refuse = set()

    def send(self, recipient, payload, message_id):
        self.calls.append((recipient, payload, message_id))
        if recipient in self.refuse:
            raise TimeoutError("private server detail")


def test_immediate_retry_is_per_recipient_and_persistent(tmp_path):
    repository = make_repository(tmp_path)
    watchlist = create_watch(repository)
    add_event(repository, watchlist)
    sender = Sender()
    sender.refuse.add("two@example.com")
    dispatcher = NotificationDispatcher(repository, email_settings(), sender)
    assert dispatcher.run_once(now=NOW) == 1
    assert [row.status for row in deliveries(repository)] == ["sent", "pending"]
    assert dispatcher.run_once(now=NOW + timedelta(seconds=30)) == 0
    sender.refuse.clear()
    restarted = NotificationDispatcher(repository, email_settings(), sender)
    assert restarted.run_once(now=NOW + timedelta(seconds=60)) == 1
    assert restarted.run_once(now=NOW + timedelta(days=1)) == 0
    assert [call[0] for call in sender.calls].count("one@example.com") == 1
    retried = [call for call in sender.calls if call[0] == "two@example.com"]
    assert len(retried) == 2
    assert retried[0][2] == retried[1][2]


def test_retries_exhaust_and_leases_are_exclusive(tmp_path):
    repository = make_repository(tmp_path)
    watchlist = create_watch(repository)
    add_event(repository, watchlist)
    sender = Sender()
    sender.refuse = {"one@example.com", "two@example.com"}
    dispatcher = NotificationDispatcher(repository, email_settings(DMF_NOTIFICATION_MAX_RETRIES="1"), sender)
    first = dispatcher.claim_delivery(NOW)
    second = dispatcher.claim_delivery(NOW)
    assert first.id != second.id
    assert dispatcher.claim_delivery(NOW) is None
    assert dispatcher.run_once(now=NOW + timedelta(minutes=16)) == 0
    assert {row.status for row in deliveries(repository)} == {"failed"}
    assert {row.attempts for row in deliveries(repository)} == {2}
    assert dispatcher.run_once(now=NOW + timedelta(days=1)) == 0


def test_weekly_frozen_window_and_no_change_report(tmp_path):
    repository = make_repository(tmp_path)
    watchlist = create_watch(repository, WatchlistNotificationMode.WEEKLY_DIGEST)
    add_event(repository, watchlist)
    sender = Sender()
    dispatcher = NotificationDispatcher(repository, email_settings(), sender)
    boundary = NOW + timedelta(hours=1)
    assert dispatcher.run_once(now=boundary - timedelta(seconds=1)) == 0
    assert dispatcher.run_once(now=boundary) == 2
    assert len(sender.calls[0][1]["events"]) == 1
    assert dispatcher.run_once(now=boundary + timedelta(minutes=1)) == 0
    assert dispatcher.run_once(now=boundary + timedelta(days=7)) == 2
    assert sender.calls[-1][1]["events"] == []
    assert repository.get(watchlist.id).last_digest_enqueued_at is not None


def test_weekly_retry_does_not_expand_payload(tmp_path):
    repository = make_repository(tmp_path)
    watchlist = create_watch(repository, WatchlistNotificationMode.WEEKLY_DIGEST)
    add_event(repository, watchlist)
    sender = Sender()
    sender.refuse = {"two@example.com"}
    dispatcher = NotificationDispatcher(repository, email_settings(), sender)
    boundary = NOW + timedelta(hours=1)
    dispatcher.run_once(now=boundary)
    add_event(repository, watchlist, now=boundary)
    sender.refuse.clear()
    dispatcher.run_once(now=boundary + timedelta(minutes=1))
    retried = [call for call in sender.calls if call[0] == "two@example.com"]
    assert len(retried) == 2
    assert retried[0][1] == retried[1][1]
    assert len(retried[1][1]["events"]) == 1


def test_disabled_global_and_changed_configuration_cancel_delivery(tmp_path):
    repository = make_repository(tmp_path)
    watchlist = create_watch(repository)
    add_event(repository, watchlist)
    sender = Sender()
    disabled = NotificationDispatcher(repository, email_settings(DMF_NOTIFICATION_ENABLED="false"), sender)
    assert disabled.run_once(now=NOW) == 0
    repository.update(watchlist.id, notification_enabled=False,
                      notification_fields={"notification_enabled"}, now=NOW)
    repository.update(watchlist.id, notification_enabled=True,
                      notification_fields={"notification_enabled"}, now=NOW)
    enabled = NotificationDispatcher(repository, email_settings(), sender)
    assert enabled.run_once(now=NOW) == 0
    assert {row.status for row in deliveries(repository)} == {"cancelled"}
    assert sender.calls == []


def test_weekly_boundary_timezone_and_missed_schedule(tmp_path):
    settings = email_settings(DMF_NOTIFICATION_TIMEZONE="Asia/Taipei")
    assert next_weekly_boundary(NOW, settings) == NOW + timedelta(hours=1)
    repository = make_repository(tmp_path)
    create_watch(repository, WatchlistNotificationMode.WEEKLY_DIGEST)
    sender = Sender()
    dispatcher = NotificationDispatcher(repository, settings, sender)
    assert dispatcher.run_once(now=NOW + timedelta(days=2)) == 2
    assert dispatcher.run_once(now=NOW + timedelta(days=2)) == 0


def test_two_workers_do_not_claim_the_same_delivery(tmp_path):
    repository = make_repository(tmp_path)
    watchlist = create_watch(repository)
    add_event(repository, watchlist)
    barrier = Barrier(2)

    def claim():
        dispatcher = NotificationDispatcher(repository, email_settings(), Sender())
        barrier.wait(timeout=5)
        return dispatcher.claim_delivery(NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(claim) for _worker in range(2)]
        claimed = [future.result() for future in futures]
    claimed_ids = [row.id for row in claimed if row is not None]
    assert claimed_ids
    assert len(claimed_ids) == len(set(claimed_ids))


def test_real_time_retry_uses_send_completion_time(tmp_path):
    repository = make_repository(tmp_path)
    watchlist = create_watch(repository)
    add_event(repository, watchlist)
    current = [NOW]

    class SlowSender:
        def send(self, recipient, payload, message_id):
            current[0] += timedelta(minutes=2)
            raise TimeoutError("temporary failure")

    dispatcher = NotificationDispatcher(repository, email_settings(), SlowSender(), clock=lambda: current[0])
    dispatcher.run_once(limit=1)
    attempted = [row for row in deliveries(repository) if row.attempts == 1][0]
    assert attempted.next_attempt_at.replace(tzinfo=timezone.utc) == NOW + timedelta(minutes=3)


def test_rollback_does_not_leave_notification_tasks(tmp_path, monkeypatch):
    repository = make_repository(tmp_path)
    watchlist = create_watch(repository)

    def reject(*args, **kwargs):
        raise RuntimeError("transaction failed")

    monkeypatch.setattr("src.dmf_watchlist.repository.enqueue_delivery", reject)
    import pytest

    with pytest.raises(RuntimeError, match="transaction failed"):
        add_event(repository, watchlist)
    assert repository.list_events(watchlist.id) == []
    assert deliveries(repository) == []