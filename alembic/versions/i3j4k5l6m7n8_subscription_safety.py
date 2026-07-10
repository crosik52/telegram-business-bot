"""subscription_safety: unique payment_charge_id + one-active-sub guard

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "i3j4k5l6m7n8"
down_revision = "h2i3j4k5l6m7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unique index on payment_charge_id (only for paid subscriptions — exclude NULLs)
    op.execute(
        """
        CREATE UNIQUE INDEX uix_user_subscriptions_charge_id
        ON user_subscriptions (payment_charge_id)
        WHERE payment_charge_id IS NOT NULL
        """
    )

    # Partial unique index: at most one active subscription per user at a time
    op.execute(
        """
        CREATE UNIQUE INDEX uix_user_subscriptions_one_active
        ON user_subscriptions (user_telegram_id)
        WHERE is_active = true
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uix_user_subscriptions_charge_id")
    op.execute("DROP INDEX IF EXISTS uix_user_subscriptions_one_active")
