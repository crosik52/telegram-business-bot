"""add admin action log table

Revision ID: a1b2c3d4e5f6
Revises: f3a1c9d2e7b4
Create Date: 2026-07-06 09:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = 'f3a1c9d2e7b4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'admin_action_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('admin_username', sa.String(length=255), nullable=True),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('target_owner_telegram_id', sa.BigInteger(), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        'ix_admin_action_log_created_at', 'admin_action_log', ['created_at']
    )


def downgrade() -> None:
    op.drop_index('ix_admin_action_log_created_at', table_name='admin_action_log')
    op.drop_table('admin_action_log')
