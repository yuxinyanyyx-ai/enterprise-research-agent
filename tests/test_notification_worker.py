from unittest.mock import MagicMock

from src.dmf_watchlist import worker
from tests.test_email_service import email_settings


def test_dispatch_failure_is_isolated():
    dispatcher = MagicMock()
    dispatcher.run_once.side_effect = RuntimeError("private SMTP details")
    assert worker.run_notifications_once(dispatcher) == 0


def test_notifications_only_worker_once(monkeypatch):
    monkeypatch.setattr("sys.argv", ["worker", "--once", "--notifications-only"])
    monkeypatch.setattr(worker, "load_settings", lambda **kwargs: email_settings())
    monkeypatch.setattr(worker, "DMFHistoryRepository", MagicMock())
    monkeypatch.setattr(worker, "DMFWatchlistRepository", MagicMock())
    query = MagicMock()
    monkeypatch.setattr(worker, "run_due_once", query)
    dispatcher = MagicMock()
    factory = MagicMock(return_value=dispatcher)
    monkeypatch.setattr(worker, "NotificationDispatcher", factory)
    worker.main()
    dispatcher.run_once.assert_called_once_with(limit=20)
    query.assert_not_called()