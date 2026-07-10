"""daily_quests: add daily_quest_completions table

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_quest_completions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("quest_id", sa.String(32), nullable=False),
        sa.Column("quest_date", sa.Date(), nullable=False),
        sa.Column("reward", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint(
            "owner_telegram_id", "quest_id", "quest_date",
            name="uq_daily_quest_completion",
        ),
    )
    op.create_index(
        "ix_daily_quest_completions_owner_date",
        "daily_quest_completions",
        ["owner_telegram_id", "quest_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_daily_quest_completions_owner_date", table_name="daily_quest_completions")
    op.drop_table("daily_quest_completions")
