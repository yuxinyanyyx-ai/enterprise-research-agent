"""create dmf watchlist tables

Revision ID: 7b13d8a94f21
Revises: fa2476c5e323
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7b13d8a94f21"
down_revision: Union[str, Sequence[str], None] = "fa2476c5e323"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dmf_watchlists",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("dmf_no", sa.String(length=255), nullable=False),
        sa.Column("normalized_dmf_no", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("interval_hours", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_eligible_snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("baseline_payload", sa.JSON(), nullable=True),
        sa.Column("consecutive_absent_count", sa.Integer(), nullable=False),
        sa.Column("absence_alerted", sa.Boolean(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_token", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["last_eligible_snapshot_id"], ["dmf_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_dmf_no", name="uq_dmf_watchlist_number"),
    )
    op.create_index("ix_dmf_watchlists_status", "dmf_watchlists", ["status"])
    op.create_index("ix_dmf_watchlists_next_run_at", "dmf_watchlists", ["next_run_at"])
    op.create_index("ix_dmf_watchlists_locked_until", "dmf_watchlists", ["locked_until"])

    op.create_table(
        "dmf_watchlist_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("watchlist_id", sa.String(length=36), nullable=False),
        sa.Column("monitor_run_id", sa.String(length=36), nullable=True),
        sa.Column("snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["monitor_run_id"], ["dmf_monitor_runs.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["dmf_snapshots.id"]),
        sa.ForeignKeyConstraint(["watchlist_id"], ["dmf_watchlists.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_dmf_watchlist_run_key"),
    )
    op.create_index("ix_dmf_watchlist_runs_watchlist_id", "dmf_watchlist_runs", ["watchlist_id"])
    op.create_index("ix_dmf_watchlist_runs_status", "dmf_watchlist_runs", ["status"])

    op.create_table(
        "dmf_watchlist_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("watchlist_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("before_payload", sa.JSON(), nullable=True),
        sa.Column("after_payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["dmf_watchlist_runs.id"]),
        sa.ForeignKeyConstraint(["snapshot_id"], ["dmf_snapshots.id"]),
        sa.ForeignKeyConstraint(["watchlist_id"], ["dmf_watchlists.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_dmf_watchlist_event_key"),
    )
    op.create_index("ix_dmf_watchlist_events_watchlist_id", "dmf_watchlist_events", ["watchlist_id"])
    op.create_index("ix_dmf_watchlist_events_run_id", "dmf_watchlist_events", ["run_id"])
    op.create_index("ix_dmf_watchlist_events_event_type", "dmf_watchlist_events", ["event_type"])
    op.create_index("ix_dmf_watchlist_events_status", "dmf_watchlist_events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_dmf_watchlist_events_status", table_name="dmf_watchlist_events")
    op.drop_index("ix_dmf_watchlist_events_event_type", table_name="dmf_watchlist_events")
    op.drop_index("ix_dmf_watchlist_events_run_id", table_name="dmf_watchlist_events")
    op.drop_index("ix_dmf_watchlist_events_watchlist_id", table_name="dmf_watchlist_events")
    op.drop_table("dmf_watchlist_events")
    op.drop_index("ix_dmf_watchlist_runs_status", table_name="dmf_watchlist_runs")
    op.drop_index("ix_dmf_watchlist_runs_watchlist_id", table_name="dmf_watchlist_runs")
    op.drop_table("dmf_watchlist_runs")
    op.drop_index("ix_dmf_watchlists_locked_until", table_name="dmf_watchlists")
    op.drop_index("ix_dmf_watchlists_next_run_at", table_name="dmf_watchlists")
    op.drop_index("ix_dmf_watchlists_status", table_name="dmf_watchlists")
    op.drop_table("dmf_watchlists")
