from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, select

from src.dmf_watchlist.models import DMFWatchlist, DMFWatchlistEvent
from src.dmf_watchlist.repository import DMFWatchlistRepository
from src.dmf_watchlist.notification_dispatcher import NotificationDispatcher
from tests.test_email_service import email_settings
from tests.test_notification_dispatcher import Sender


def test_upgrade_legacy_data_and_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "d8e4c0a1b2f3")
    engine = create_engine(url)
    metadata = MetaData()
    watchlists = Table("dmf_watchlists", metadata, autoload_with=engine)
    runs = Table("dmf_watchlist_runs", metadata, autoload_with=engine)
    events = Table("dmf_watchlist_events", metadata, autoload_with=engine)
    now = datetime(2026, 9, 7, 2, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(watchlists.insert().values(
            id="old", dmf_no="001", normalized_dmf_no="001", status="active",
            interval_hours=24, consecutive_absent_count=0, absence_alerted=False,
            failure_count=0, created_at=now, updated_at=now, notification_enabled=True,
            notification_emails=["old@example.com"], notification_mode="weekly_digest",
        ))
        connection.execute(runs.insert().values(
            id="old-run", watchlist_id="old", trigger="manual", status="changed",
            scheduled_for=now, idempotency_key="old-key", started_at=now,
        ))
        connection.execute(events.insert().values(
            id="old-event", watchlist_id="old", run_id="old-run", event_type="added",
            status="unread", dedupe_key="old-event-key", created_at=now,
        ))
    command.upgrade(config, "head")
    repository = DMFWatchlistRepository(engine)
    with repository.session_factory() as session:
        assert session.get(DMFWatchlist, "old").notification_generation == 0
        assert session.get(DMFWatchlistEvent, "old-event").notification_generation == -1
    sender = Sender()
    dispatcher = NotificationDispatcher(repository, email_settings(), sender)
    assert dispatcher.run_once(now=now) == 0
    assert sender.calls == []
    assert repository.get("old").notification_since is not None
    command.downgrade(config, "d8e4c0a1b2f3")
    assert "dmf_notification_deliveries" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(select(watchlists.c.dmf_no)) == "001"
        assert connection.scalar(select(events.c.id)) == "old-event"
    engine.dispose()