"""add durable per-recipient notification outbox

Revision ID: e91a26b7c804
Revises: d8e4c0a1b2f3
"""

from alembic import op
import sqlalchemy as sa


revision = "e91a26b7c804"
down_revision = "d8e4c0a1b2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("dmf_watchlists") as batch:
        batch.add_column(sa.Column("notification_generation", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("notification_since", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_digest_enqueued_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("dmf_watchlist_events") as batch:
        batch.add_column(sa.Column("notification_generation", sa.Integer(), nullable=False, server_default="-1"))
    op.create_table(
        "dmf_notification_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("watchlist_id", sa.String(36), sa.ForeignKey("dmf_watchlists.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(700), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("lock_token", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.UniqueConstraint("dedupe_key", name="uq_dmf_notification_delivery_key"),
    )
    for column in ("watchlist_id", "status", "next_attempt_at"):
        op.create_index(f"ix_dmf_notification_deliveries_{column}", "dmf_notification_deliveries", [column])


def downgrade() -> None:
    op.drop_table("dmf_notification_deliveries")
    with op.batch_alter_table("dmf_watchlist_events") as batch:
        batch.drop_column("notification_generation")
    with op.batch_alter_table("dmf_watchlists") as batch:
        batch.drop_column("last_digest_enqueued_at")
        batch.drop_column("notification_since")
        batch.drop_column("notification_generation")