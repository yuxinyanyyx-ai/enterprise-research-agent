"""SQLAlchemy models for DMF watchlists and their event inbox."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.dmf_history.models import Base


class DMFWatchlist(Base):
    __tablename__ = "dmf_watchlists"
    __table_args__ = (UniqueConstraint("normalized_dmf_no", name="uq_dmf_watchlist_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dmf_no: Mapped[str] = mapped_column(String(255))
    normalized_dmf_no: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), index=True)
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_eligible_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("dmf_snapshots.id"), nullable=True
    )
    baseline_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expiration_alerted_valid_date: Mapped[str | None] = mapped_column(
        String(10), nullable=True
    )
    expiration_alert_version: Mapped[int] = mapped_column(Integer, default=0)
    notification_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    notification_emails: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    notification_mode: Mapped[str] = mapped_column(
        String(32), default="immediate", server_default="immediate"
    )
    notification_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    notification_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_digest_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_absent_count: Mapped[int] = mapped_column(Integer, default=0)
    absence_alerted: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lock_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DMFWatchlistRun(Base):
    __tablename__ = "dmf_watchlist_runs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_dmf_watchlist_run_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watchlist_id: Mapped[str] = mapped_column(ForeignKey("dmf_watchlists.id"), index=True)
    monitor_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("dmf_monitor_runs.id"), nullable=True
    )
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("dmf_snapshots.id"), nullable=True)
    trigger: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)


class DMFWatchlistEvent(Base):
    __tablename__ = "dmf_watchlist_events"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_dmf_watchlist_event_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watchlist_id: Mapped[str] = mapped_column(ForeignKey("dmf_watchlists.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("dmf_watchlist_runs.id"), index=True)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("dmf_snapshots.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    before_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_generation: Mapped[int] = mapped_column(Integer, default=-1, server_default="-1")


class DMFNotificationDelivery(Base):
    __tablename__ = "dmf_notification_deliveries"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_dmf_notification_delivery_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watchlist_id: Mapped[str] = mapped_column(ForeignKey("dmf_watchlists.id"), index=True)
    generation: Mapped[int] = mapped_column(Integer)
    recipient: Mapped[str] = mapped_column(String(320))
    mode: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    dedupe_key: Mapped[str] = mapped_column(String(700))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_token: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
