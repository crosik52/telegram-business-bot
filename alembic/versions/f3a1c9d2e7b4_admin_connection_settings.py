"""add admin-controlled connection settings

Revision ID: f3a1c9d2e7b4
Revises: d90b51a8fa5b
Create Date: 2026-07-05 19:30:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f3a1c9d2e7b4'
down_revision: str | None = 'd90b51a8fa5b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'business_connections',
        sa.Column(
            'notifications_enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        'business_connections',
        sa.Column(
            'is_blocked',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('business_connections', 'is_blocked')
    op.drop_column('business_connections', 'notifications_enabled')
