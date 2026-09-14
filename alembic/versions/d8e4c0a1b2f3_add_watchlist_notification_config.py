"""add watchlist notification configuration

Revision ID: d8e4c0a1b2f3
Revises: c42f81a6d903
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e4c0a1b2f3"
down_revision: Union[str, Sequence[str], None] = "c42f81a6d903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dmf_watchlists") as batch_op:
        batch_op.add_column(
            sa.Column(
                "notification_enabled",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "notification_emails",
                sa.JSON(),
                server_default=sa.text("'[]'"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "notification_mode",
                sa.String(length=32),
                server_default=sa.text("'immediate'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("dmf_watchlists") as batch_op:
        batch_op.drop_column("notification_mode")
        batch_op.drop_column("notification_emails")
        batch_op.drop_column("notification_enabled")