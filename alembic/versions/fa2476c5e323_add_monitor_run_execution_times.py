"""add monitor run execution times

Revision ID: fa2476c5e323
Revises: 0117e988992b
Create Date: 2026-09-03 13:24:38.479861
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'fa2476c5e323'
down_revision: Union[str, Sequence[str], None] = '0117e988992b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('dmf_monitor_runs') as batch_op:
        batch_op.add_column(
            sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.alter_column(
            'started_at', existing_type=sa.DateTime(timezone=True), nullable=True
        )
        batch_op.alter_column(
            'queried_at', existing_type=sa.DateTime(timezone=True), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table('dmf_monitor_runs') as batch_op:
        batch_op.alter_column(
            'queried_at', existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch_op.alter_column(
            'started_at', existing_type=sa.DateTime(timezone=True), nullable=False
        )
        batch_op.drop_column('ended_at')