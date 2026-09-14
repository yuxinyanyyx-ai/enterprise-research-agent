"""Execution orchestration for watched DMF observations."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.dmf_history.comparison import normalize_text
from src.dmf_history.repository import DMFHistoryRepository
from src.dmf_query.multi_query_service import search_dmf_queries
from src.schemas.dmf import DMFCollectionStatus, DMFRecord, DMFSearchResult
from src.schemas.watchlist import (
    WatchlistCreate,
    WatchlistEventView,
    WatchlistObservation,
    WatchlistRunView,
    WatchlistRunStatus,
    WatchlistRunTrigger,
    WatchlistNotificationMode,
    WatchlistUpdate,
    WatchlistView,
)
from src.settings import get_settings

from .repository import DMFWatchlistRepository, WatchlistClaim
from .state import evaluate_observation

PUBLIC_QUERY_ERROR_CODE = "WATCHLIST_QUERY_ERROR"
PUBLIC_QUERY_ERROR_MESSAGE = "DMF 关注查询暂时失败，请稍后重试。"
PUBLIC_HISTORY_ERROR_CODE = "WATCHLIST_HISTORY_ERROR"
PUBLIC_HISTORY_ERROR_MESSAGE = "DMF 关注查询已完成，但历史快照暂时不可用。"

SearchFunction = Callable[..., dict]
NowFunction = Callable[[], datetime]
TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")


class WatchlistUnavailableError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def create_watchlist_service() -> "DMFWatchlistService":
    settings = get_settings()
    if not settings.dmf_watchlist_enabled:
        raise WatchlistUnavailableError("DMF Watchlist 暂未启用")
    history = DMFHistoryRepository(settings.database_url)
    repository = DMFWatchlistRepository(history.engine)
    return DMFWatchlistService(repository, history)


class DMFWatchlistService:
    def __init__(
        self,
        repository: DMFWatchlistRepository,
        history_repository: DMFHistoryRepository,
        *,
        search: SearchFunction = search_dmf_queries,
        now: NowFunction | None = None,
    ) -> None:
        self.repository = repository
        self.history_repository = history_repository
        self.search = search
        self.now = now or (lambda: datetime.now(timezone.utc))

    def add(
        self,
        dmf_no: str,
        interval_hours: int = 24,
        *,
        notification_enabled: bool | None = None,
        notification_emails: list[str] | None = None,
        notification_mode: WatchlistNotificationMode | None = None,
        notification_fields: set[str] | None = None,
    ) -> WatchlistView:
        kwargs = {}
        if notification_enabled is not None:
            kwargs["notification_enabled"] = notification_enabled
        if notification_emails is not None:
            kwargs["notification_emails"] = notification_emails
        if notification_mode is not None:
            kwargs["notification_mode"] = notification_mode
        payload = WatchlistCreate(
            dmf_no=dmf_no, interval_hours=interval_hours, **kwargs
        )
        fields = notification_fields or {
            field
            for field in payload.model_fields_set
            if field.startswith("notification_")
        }
        return WatchlistView.model_validate(
            self.repository.create_or_restore(
                payload.dmf_no,
                payload.interval_hours,
                notification_enabled=bool(payload.notification_enabled),
                notification_emails=payload.notification_emails,
                notification_mode=payload.notification_mode or WatchlistNotificationMode.IMMEDIATE,
                notification_fields=fields,
            )
        )

    def configure_notifications(
        self,
        *,
        watchlist_id: str = "",
        dmf_no: str = "",
        notification_enabled: bool | None = None,
        notification_emails: list[str] | None = None,
        notification_mode: WatchlistNotificationMode | None = None,
    ) -> WatchlistView:
        values = {
            name: value for name, value in {
                "notification_enabled": notification_enabled,
                "notification_emails": notification_emails,
                "notification_mode": notification_mode,
            }.items() if value is not None
        }
        if not values:
            raise ValueError("请提供要修改的通知开关、邮箱或通知模式")
        payload = WatchlistUpdate.model_validate(values)
        watchlist = self.resolve(watchlist_id=watchlist_id, dmf_no=dmf_no)
        return WatchlistView.model_validate(self.repository.update(
            watchlist.id,
            notification_enabled=payload.notification_enabled,
            notification_emails=payload.notification_emails,
            notification_mode=payload.notification_mode,
            notification_fields=payload.model_fields_set,
        ))

    def remove(self, *, watchlist_id: str = "", dmf_no: str = "") -> WatchlistView:
        watchlist = self.resolve(watchlist_id=watchlist_id, dmf_no=dmf_no)
        self.repository.soft_delete(watchlist.id)
        return watchlist

    def list_watchlists(self) -> list[WatchlistView]:
        return [WatchlistView.model_validate(row) for row in self.repository.list()]

    def resolve(self, *, watchlist_id: str = "", dmf_no: str = "") -> WatchlistView:
        normalized_id = watchlist_id.strip()
        normalized_dmf_no = normalize_text(dmf_no)
        if normalized_id:
            watchlist = WatchlistView.model_validate(self.repository.get(normalized_id))
            if normalized_dmf_no and normalize_text(watchlist.dmf_no) != normalized_dmf_no:
                raise ValueError("关注项 ID 与 DMF 编号不匹配")
            return watchlist
        if not normalized_dmf_no:
            raise ValueError("请提供关注项 ID 或 DMF 编号")
        for row in self.repository.list():
            if normalize_text(row.dmf_no) == normalized_dmf_no:
                return WatchlistView.model_validate(row)
        from .repository import WatchlistNotFoundError

        raise WatchlistNotFoundError(dmf_no)

    def list_events(
        self, *, watchlist_id: str = "", dmf_no: str = ""
    ) -> tuple[WatchlistView, list[WatchlistEventView]]:
        watchlist = self.resolve(watchlist_id=watchlist_id, dmf_no=dmf_no)
        events = [
            WatchlistEventView.model_validate(row)
            for row in self.repository.list_events(watchlist.id)
        ]
        return watchlist, events

    def acknowledge_event(
        self,
        event_id: str,
        *,
        watchlist_id: str = "",
        dmf_no: str = "",
    ) -> WatchlistEventView:
        if not event_id.strip():
            raise ValueError("请提供关注事件 ID")
        watchlist = self.resolve(watchlist_id=watchlist_id, dmf_no=dmf_no)
        return WatchlistEventView.model_validate(
            self.repository.acknowledge_event(watchlist.id, event_id.strip())
        )

    def run_manual(
        self, watchlist_id: str, *, idempotency_key: str | None = None
    ) -> WatchlistRunView:
        claim = self.repository.claim_manual(watchlist_id)
        return WatchlistRunView.model_validate(
            self.execute_claim(
                claim,
                WatchlistRunTrigger.MANUAL,
                idempotency_key or f"manual:{watchlist_id}:{uuid4()}",
            )
        )

    def execute_claim(
        self,
        claim: WatchlistClaim,
        trigger: WatchlistRunTrigger,
        idempotency_key: str,
    ):
        try:
            run = self.repository.start_run(claim, trigger, idempotency_key)
        except Exception:
            self.repository.release_failed_claim(claim)
            raise
        if run.status != WatchlistRunStatus.RUNNING.value:
            self.repository.release_failed_claim(claim)
            return run

        watchlist = self.repository.get(claim.watchlist_id)
        previous_record = (
            DMFRecord.model_validate(watchlist.baseline_payload)
            if watchlist.baseline_payload
            else None
        )
        try:
            raw_search = self.search(
                dmf_no=claim.dmf_no,
                applicant_name="",
                ingredients=[],
                history_repository=self.history_repository,
            )
            search_result = DMFSearchResult.model_validate(raw_search)
        except Exception:
            observation = _failed_observation(watchlist, WatchlistRunStatus.QUERY_FAILED)
            return self.repository.finalize_run(
                claim=claim,
                run_id=run.id,
                observation=observation,
                monitor_run_id=None,
                snapshot_id=None,
                previous_record=previous_record,
                current_record=None,
                error_code=PUBLIC_QUERY_ERROR_CODE,
                error_message=PUBLIC_QUERY_ERROR_MESSAGE,
            )

        if len(search_result.results) != 1:
            observation = _failed_observation(watchlist, WatchlistRunStatus.QUERY_FAILED)
            return self.repository.finalize_run(
                claim=claim,
                run_id=run.id,
                observation=observation,
                monitor_run_id=None,
                snapshot_id=None,
                previous_record=previous_record,
                current_record=None,
                error_code=PUBLIC_QUERY_ERROR_CODE,
                error_message=PUBLIC_QUERY_ERROR_MESSAGE,
            )

        query_result = search_result.results[0]
        history = query_result.history
        if history is None or history.history_status in {"snapshot_failed", "monitor_run_failed"}:
            observation = _failed_observation(watchlist, WatchlistRunStatus.HISTORY_FAILED)
            return self.repository.finalize_run(
                claim=claim,
                run_id=run.id,
                observation=observation,
                monitor_run_id=history.monitor_run_id if history else None,
                snapshot_id=history.snapshot_id if history else None,
                previous_record=previous_record,
                current_record=None,
                error_code=PUBLIC_HISTORY_ERROR_CODE,
                error_message=PUBLIC_HISTORY_ERROR_MESSAGE,
            )

        target = normalize_text(claim.dmf_no)
        matching = [
            record for record in query_result.records if normalize_text(record.dmf_no) == target
        ]
        current_record = matching[0] if len(matching) == 1 else None
        observation = evaluate_observation(
            collection_status=(
                query_result.collection_status or DMFCollectionStatus.FAILED
            ),
            current_record=current_record,
            current_identity_ambiguous=len(matching) > 1,
            baseline_record=previous_record,
            consecutive_absent_count=watchlist.consecutive_absent_count,
            absence_alerted=watchlist.absence_alerted,
            observed_date=self.now().astimezone(TAIPEI_TIMEZONE).date(),
            expiration_alerted_valid_date=(
                watchlist.expiration_alerted_valid_date
            ),
        )
        error_code = (
            PUBLIC_QUERY_ERROR_CODE
            if observation.run_status == WatchlistRunStatus.QUERY_FAILED
            else None
        )
        return self.repository.finalize_run(
            claim=claim,
            run_id=run.id,
            observation=observation,
            monitor_run_id=history.monitor_run_id,
            snapshot_id=history.snapshot_id,
            previous_record=previous_record,
            current_record=current_record,
            error_code=error_code,
            error_message=PUBLIC_QUERY_ERROR_MESSAGE if error_code else None,
        )


def scheduled_idempotency_key(claim: WatchlistClaim) -> str:
    scheduled_for = claim.scheduled_for
    if scheduled_for.tzinfo is None:
        scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
    return f"{claim.watchlist_id}:{scheduled_for.astimezone(timezone.utc).isoformat()}"


def _failed_observation(watchlist, status: WatchlistRunStatus) -> WatchlistObservation:
    return WatchlistObservation(
        run_status=status,
        consecutive_absent_count=watchlist.consecutive_absent_count,
        absence_alerted=watchlist.absence_alerted,
        baseline_record=(
            DMFRecord.model_validate(watchlist.baseline_payload)
            if watchlist.baseline_payload
            else None
        ),
        expiration_alerted_valid_date=watchlist.expiration_alerted_valid_date,
    )
