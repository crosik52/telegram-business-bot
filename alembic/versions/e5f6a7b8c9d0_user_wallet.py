"""Add user_wallets table for coin balance and casino

Revision ID: e5f6a7b8c9d0
Revises: a1b2c3d4e5f6
Create Date: 2026-07-09 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_wallets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_earned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_spent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_daily_claim", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("owner_telegram_id", name="uq_user_wallets_owner"),
    )
    op.create_index(
        "ix_user_wallets_owner_telegram_id",
        "user_wallets",
        ["owner_telegram_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_user_wallets_owner_telegram_id", table_name="user_wallets")
    op.drop_table("user_wallets")
