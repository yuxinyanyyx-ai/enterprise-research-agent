"""Resource API for team-shared DMF watchlists."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Header, HTTPException, Response, status

from src.dmf_history.repository import DMFHistoryRepository
from src.dmf_watchlist.repository import (
    DMFWatchlistRepository,
    WatchlistConflictError,
    WatchlistNotFoundError,
)
from src.dmf_watchlist.service import DMFWatchlistService
from src.schemas.watchlist import (
    WatchlistCreate,
    WatchlistEventView,
    WatchlistRunView,
    WatchlistUpdate,
    WatchlistView,
)
from src.settings import get_settings

router = APIRouter(prefix="/api/watchlists", tags=["DMF Watchlist"])


@lru_cache(maxsize=1)
def _get_service() -> DMFWatchlistService:
    settings = get_settings()
    if not settings.dmf_watchlist_enabled:
        raise RuntimeError("DMF Watchlist 未启用")
    history = DMFHistoryRepository(settings.database_url)
    repository = DMFWatchlistRepository(history.engine)
    return DMFWatchlistService(repository, history)


def _service() -> DMFWatchlistService:
    try:
        return _get_service()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DMF Watchlist 暂未启用",
        ) from exc


@router.post("", response_model=WatchlistView, status_code=status.HTTP_201_CREATED)
def create_watchlist(payload: WatchlistCreate) -> WatchlistView:
    try:
        row = _service().add(
            payload.dmf_no,
            payload.interval_hours,
            notification_enabled=payload.notification_enabled,
            notification_emails=payload.notification_emails,
            notification_mode=payload.notification_mode,
            notification_fields={
                field
                for field in payload.model_fields_set
                if field.startswith("notification_")
            },
        )
    except WatchlistConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return WatchlistView.model_validate(row)


@router.get("", response_model=list[WatchlistView])
def list_watchlists() -> list[WatchlistView]:
    return _service().list_watchlists()


@router.get("/{watchlist_id}", response_model=WatchlistView)
def get_watchlist(watchlist_id: str) -> WatchlistView:
    try:
        return WatchlistView.model_validate(_service().repository.get(watchlist_id))
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关注项不存在") from exc


@router.patch("/{watchlist_id}", response_model=WatchlistView)
def update_watchlist(watchlist_id: str, payload: WatchlistUpdate) -> WatchlistView:
    try:
        row = _service().repository.update(
            watchlist_id,
            status=payload.status,
            interval_hours=payload.interval_hours,
            notification_enabled=payload.notification_enabled,
            notification_emails=payload.notification_emails,
            notification_mode=payload.notification_mode,
            notification_fields={
                field
                for field in payload.model_fields_set
                if field.startswith("notification_")
            },
        )
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关注项不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return WatchlistView.model_validate(row)


@router.delete("/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist(watchlist_id: str) -> Response:
    try:
        _service().remove(watchlist_id=watchlist_id)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关注项不存在") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{watchlist_id}/runs", response_model=WatchlistRunView)
def run_watchlist(
    watchlist_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> WatchlistRunView:
    try:
        run = _service().run_manual(watchlist_id, idempotency_key=idempotency_key)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关注项不存在") from exc
    except WatchlistConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return WatchlistRunView.model_validate(run)


@router.get("/{watchlist_id}/runs", response_model=list[WatchlistRunView])
def list_watchlist_runs(watchlist_id: str) -> list[WatchlistRunView]:
    try:
        rows = _service().repository.list_runs(watchlist_id)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关注项不存在") from exc
    return [WatchlistRunView.model_validate(row) for row in rows]


@router.get("/{watchlist_id}/events", response_model=list[WatchlistEventView])
def list_watchlist_events(watchlist_id: str) -> list[WatchlistEventView]:
    try:
        _, rows = _service().list_events(watchlist_id=watchlist_id)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关注项不存在") from exc
    return rows


@router.post(
    "/{watchlist_id}/events/{event_id}/ack", response_model=WatchlistEventView
)
def acknowledge_watchlist_event(watchlist_id: str, event_id: str) -> WatchlistEventView:
    try:
        row = _service().acknowledge_event(event_id, watchlist_id=watchlist_id)
    except WatchlistNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="关注事件不存在") from exc
    return WatchlistEventView.model_validate(row)
