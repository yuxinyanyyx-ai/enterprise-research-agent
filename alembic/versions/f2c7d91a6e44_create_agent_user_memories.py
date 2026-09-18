"""create user-scoped Agent memories

Revision ID: f2c7d91a6e44
Revises: e91a26b7c804
"""

from alembic import op
import sqlalchemy as sa


revision = "f2c7d91a6e44"
down_revision = "e91a26b7c804"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_user_memories",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("tenant_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("memory_key", sa.String(length=128), nullable=False),
        sa.Column("memory_type", sa.String(length=32), nullable=False, server_default="preference"),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="explicit_user"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("audit_note", sa.Text(), nullable=True),
        sa.UniqueConstraint("tenant_id", "user_id", "memory_key", name="uq_agent_user_memory_key"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "idempotency_key",
            name="uq_agent_user_memory_idempotency",
        ),
    )
    op.create_index(
        "ix_agent_user_memories_scope_status",
        "agent_user_memories",
        ["tenant_id", "user_id", "status"],
    )
    op.create_index(
        "ix_agent_user_memories_status",
        "agent_user_memories",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_user_memories_status", table_name="agent_user_memories")
    op.drop_index("ix_agent_user_memories_scope_status", table_name="agent_user_memories")
    op.drop_table("agent_user_memories")