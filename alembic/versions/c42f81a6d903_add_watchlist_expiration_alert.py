"""add watchlist expiration alert state

Revision ID: c42f81a6d903
Revises: 7b13d8a94f21
Create Date: 2026-09-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c42f81a6d903"
down_revision: Union[str, Sequence[str], None] = "7b13d8a94f21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("dmf_watchlists") as batch_op:
        batch_op.add_column(
            sa.Column("expiration_alerted_valid_date", sa.String(length=10), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "expiration_alert_version",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )

    with op.batch_alter_table("dmf_watchlist_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "warnings",
                sa.JSON(),
                server_default=sa.text("'[]'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("dmf_watchlist_runs") as batch_op:
        batch_op.drop_column("warnings")

    with op.batch_alter_table("dmf_watchlists") as batch_op:
        batch_op.drop_column("expiration_alert_version")
        batch_op.drop_column("expiration_alerted_valid_date")
