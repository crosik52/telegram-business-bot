"""subscription: add subscription_config and user_subscriptions tables

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2026-07-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h2i3j4k5l6m7"
down_revision = "g1h2i3j4k5l6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscription_config",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("price_stars", sa.Integer, nullable=False, server_default=sa.text("99")),
        sa.Column("duration_days", sa.Integer, nullable=False, server_default=sa.text("30")),
        sa.Column("title", sa.String(100), nullable=False, server_default="Premium подписка"),
        sa.Column(
            "description",
            sa.String(255),
            nullable=False,
            server_default="Бонусы и привилегии для подписчиков",
        ),
        sa.Column("benefits", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_telegram_id", sa.BigInteger, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granted_by_admin", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("payment_charge_id", sa.String(255), nullable=True),
        sa.Column("stars_paid", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_user_subscriptions_user_telegram_id",
        "user_subscriptions",
        ["user_telegram_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_subscriptions_user_telegram_id", table_name="user_subscriptions")
    op.drop_table("user_subscriptions")
    op.drop_table("subscription_config")
