"""create minimal agent session metadata

Revision ID: a3b4c5d6e7f8
Revises: f2c7d91a6e44
"""

from alembic import op
import sqlalchemy as sa


revision = "a3b4c5d6e7f8"
down_revision = "f2c7d91a6e44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("session_id", sa.String(length=64), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("user_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("awaiting_resume", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_sessions_scope",
        "agent_sessions",
        ["tenant_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_sessions_scope", table_name="agent_sessions")
    op.drop_table("agent_sessions")