"""pet_system_v2: add mood, xp, level, personality, play/cuddle tracking

Revision ID: g1h2i3j4k5l6
Revises: 9a8b7c6d5e4f
Create Date: 2026-07-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "g1h2i3j4k5l6"
down_revision = "9a8b7c6d5e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_pets", sa.Column("mood",           sa.Integer(),     nullable=False, server_default="100"))
    op.add_column("chat_pets", sa.Column("xp",             sa.Integer(),     nullable=False, server_default="0"))
    op.add_column("chat_pets", sa.Column("level",          sa.Integer(),     nullable=False, server_default="1"))
    op.add_column("chat_pets", sa.Column("personality",    sa.String(20),    nullable=False, server_default="playful"))
    op.add_column("chat_pets", sa.Column("last_played_at",  sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_pets", sa.Column("last_cuddled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chat_pets", sa.Column("total_feedings", sa.Integer(),     nullable=False, server_default="0"))
    op.add_column("chat_pets", sa.Column("total_plays",    sa.Integer(),     nullable=False, server_default="0"))
    op.add_column("chat_pets", sa.Column("total_cuddles",  sa.Integer(),     nullable=False, server_default="0"))
    op.add_column("chat_pets", sa.Column("feed_streak",    sa.Integer(),     nullable=False, server_default="0"))


def downgrade() -> None:
    for col in ("mood","xp","level","personality","last_played_at","last_cuddled_at",
                "total_feedings","total_plays","total_cuddles","feed_streak"):
        op.drop_column("chat_pets", col)
