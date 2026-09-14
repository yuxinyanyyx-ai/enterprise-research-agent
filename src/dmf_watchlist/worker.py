"""Independent worker that executes due DMF watchlists."""

from __future__ import annotations

import argparse
import logging
import time

from src.dmf_history.repository import DMFHistoryRepository
from src.settings import load_settings

from .repository import DMFWatchlistRepository
from .service import DMFWatchlistService, scheduled_idempotency_key
from .notification_dispatcher import NotificationDispatcher
from src.schemas.watchlist import WatchlistRunTrigger

logger = logging.getLogger(__name__)


def run_due_once(
    repository: DMFWatchlistRepository,
    service: DMFWatchlistService,
    *,
    limit: int = 20,
) -> int:
    claims = repository.claim_due(limit=limit)
    for claim in claims:
        try:
            service.execute_claim(
                claim,
                WatchlistRunTrigger.SCHEDULED,
                scheduled_idempotency_key(claim),
            )
        except Exception:
            repository.release_failed_claim(claim)
            logger.exception("Watchlist 执行失败，watchlist_id=%s", claim.watchlist_id)
    return len(claims)


def run_notifications_once(dispatcher: NotificationDispatcher, *, limit: int = 20) -> int:
    try:
        return dispatcher.run_once(limit=limit)
    except Exception as exc:
        logger.error("DMF notification dispatch failed: %s", type(exc).__name__)
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="执行到期的 DMF Watchlist")
    parser.add_argument("--once", action="store_true", help="执行一轮后退出")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--notifications-only", action="store_true", help="只处理邮件队列和周报，不执行 DMF 查询")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")

    settings = load_settings(require_token=False)
    if not settings.dmf_watchlist_enabled:
        raise SystemExit("DMF_WATCHLIST_ENABLED 未启用")
    if args.notifications_only and not settings.notification_enabled:
        raise SystemExit("DMF_NOTIFICATION_ENABLED 未启用")
    history = DMFHistoryRepository(settings.database_url)
    repository = DMFWatchlistRepository(history.engine)
    service = DMFWatchlistService(repository, history)
    dispatcher = NotificationDispatcher(repository, settings)

    while True:
        if not args.notifications_only:
            run_due_once(repository, service, limit=args.limit)
        run_notifications_once(dispatcher, limit=args.limit)
        if args.once:
            return
        time.sleep(settings.dmf_watchlist_poll_seconds)


if __name__ == "__main__":
    main()
