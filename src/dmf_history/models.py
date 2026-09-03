"""SQLAlchemy models for auditable DMF query history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DMFMonitorRun(Base):
    __tablename__ = "dmf_monitor_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    query_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    fingerprint_version: Mapped[int] = mapped_column(Integer, default=1)
    query_payload: Mapped[dict] = mapped_column(JSON)
    collection_status: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    queried_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    upstream_total: Mapped[int] = mapped_column(Integer, default=0)
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    total_pages: Mapped[int] = mapped_column(Integer, default=0)
    successful_pages: Mapped[int] = mapped_column(Integer, default=0)
    failed_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    history_status: Mapped[str] = mapped_column(String(32), default="pending")
    history_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class DMFSnapshot(Base):
    __tablename__ = "dmf_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    monitor_run_id: Mapped[str] = mapped_column(
        ForeignKey("dmf_monitor_runs.id"), unique=True
    )
    query_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    previous_baseline_id: Mapped[str | None] = mapped_column(
        ForeignKey("dmf_snapshots.id"), nullable=True
    )
    queried_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[dict] = mapped_column(JSON)
    raw_payload: Mapped[list] = mapped_column(JSON)
    raw_content_hash: Mapped[str] = mapped_column(String(64))
    normalized_payload_hash: Mapped[str] = mapped_column(String(64))
    baseline_eligible: Mapped[bool] = mapped_column(Boolean, default=False)


class DMFSnapshotRecord(Base):
    __tablename__ = "dmf_snapshot_records"
    __table_args__ = (Index("ix_dmf_snapshot_records_business_key", "business_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("dmf_snapshots.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    dmf_no: Mapped[str | None] = mapped_column(String(255), nullable=True)
    applicant_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingredient: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_date: Mapped[str | None] = mapped_column(String(255), nullable=True)
    business_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    identity_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    match_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    unmatched_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DMFChangeEventModel(Base):
    __tablename__ = "dmf_change_events"
    __table_args__ = (UniqueConstraint("event_key", name="uq_dmf_change_event_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(64))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("dmf_snapshots.id"), index=True)
    previous_snapshot_id: Mapped[str] = mapped_column(ForeignKey("dmf_snapshots.id"))
    change_type: Mapped[str] = mapped_column(String(32), index=True)
    business_key: Mapped[str] = mapped_column(String(255), index=True)
    before_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DMFBaseline(Base):
    __tablename__ = "dmf_baselines"

    query_fingerprint: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("dmf_snapshots.id"), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))