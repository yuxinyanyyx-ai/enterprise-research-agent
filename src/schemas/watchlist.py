"""Contracts for persistent DMF watchlists."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.schemas.dmf import DMFRecord


class WatchlistStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DELETED = "deleted"


class WatchlistRunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class WatchlistRunStatus(StrEnum):
    RUNNING = "running"
    NO_CHANGE = "no_change"
    CHANGED = "changed"
    QUERY_FAILED = "query_failed"
    HISTORY_FAILED = "history_failed"
    IDENTITY_AMBIGUOUS = "identity_ambiguous"


class WatchlistEventType(StrEnum):
    ADDED = "added"
    FIELD_CHANGED = "field_changed"
    ABSENT_CONFIRMED = "absent_confirmed"
    REAPPEARED = "reappeared"
    VALID_DATE_EXPIRED = "valid_date_expired"


class WatchlistEventStatus(StrEnum):
    UNREAD = "unread"
    ACKNOWLEDGED = "acknowledged"


class WatchlistCreate(BaseModel):
    dmf_no: str = Field(min_length=1, max_length=255)
    interval_hours: int = Field(default=24, ge=1, le=168)


class WatchlistUpdate(BaseModel):
    status: WatchlistStatus | None = None
    interval_hours: int | None = Field(default=None, ge=1, le=168)


class WatchlistView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator(
        "next_run_at", "last_run_at", "created_at", "updated_at", mode="before"
    )
    @classmethod
    def normalize_datetimes(cls, value):
        return _as_utc(value)

    id: str
    dmf_no: str
    status: WatchlistStatus
    interval_hours: int
    next_run_at: datetime | None
    last_run_at: datetime | None
    consecutive_absent_count: int
    absence_alerted: bool
    failure_count: int
    created_at: datetime
    updated_at: datetime


class WatchlistRunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("scheduled_for", "started_at", "ended_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value):
        return _as_utc(value)

    id: str
    watchlist_id: str
    trigger: WatchlistRunTrigger
    status: WatchlistRunStatus
    monitor_run_id: str | None
    snapshot_id: str | None
    scheduled_for: datetime
    started_at: datetime
    ended_at: datetime | None
    error_code: str | None
    error_message: str | None
    warnings: list[str] = Field(default_factory=list)


class WatchlistEventView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_validator("created_at", "acknowledged_at", mode="before")
    @classmethod
    def normalize_datetimes(cls, value):
        return _as_utc(value)

    id: str
    watchlist_id: str
    run_id: str
    event_type: WatchlistEventType
    before_payload: dict | None
    after_payload: dict | None
    status: WatchlistEventStatus
    created_at: datetime
    acknowledged_at: datetime | None


class WatchlistObservation(BaseModel):
    run_status: WatchlistRunStatus
    consecutive_absent_count: int
    absence_alerted: bool
    baseline_record: DMFRecord | None = None
    expiration_alerted_valid_date: str | None = None
    normalized_valid_date: str | None = None
    event_types: list[WatchlistEventType] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _as_utc(value):
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
