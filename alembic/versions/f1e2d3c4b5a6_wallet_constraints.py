"""Add non-negative CHECK constraints to user_wallets

Revision ID: f1e2d3c4b5a6
Revises: e5f6a7b8c9d0
Create Date: 2026-07-09 00:00:01.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1e2d3c4b5a6"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "chk_wallet_balance_non_negative",
        "user_wallets",
        sa.text("balance >= 0"),
    )
    op.create_check_constraint(
        "chk_wallet_earned_non_negative",
        "user_wallets",
        sa.text("total_earned >= 0"),
    )
    op.create_check_constraint(
        "chk_wallet_spent_non_negative",
        "user_wallets",
        sa.text("total_spent >= 0"),
    )


def downgrade() -> None:
    op.drop_constraint("chk_wallet_balance_non_negative", "user_wallets")
    op.drop_constraint("chk_wallet_earned_non_negative",  "user_wallets")
    op.drop_constraint("chk_wallet_spent_non_negative",   "user_wallets")
