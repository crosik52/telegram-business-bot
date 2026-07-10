"""chat_pets: add chat_pets table for shared virtual pets

Revision ID: f2a3b4c5d6e7
Revises: e7f8a9b0c1d2
Create Date: 2026-07-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_pets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("owner_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("pet_name", sa.String(50), nullable=False),
        sa.Column("species", sa.String(20), nullable=False),
        sa.Column("interlocutor_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("is_alive", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "born_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("last_fed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("died_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("death_cause", sa.String(20), nullable=True),
    )
    op.create_index(
        "ix_chat_pets_owner",
        "chat_pets",
        ["owner_telegram_id"],
    )
    op.create_index(
        "ix_chat_pets_owner_chat_alive",
        "chat_pets",
        ["owner_telegram_id", "chat_id", "is_alive"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_pets_owner_chat_alive", table_name="chat_pets")
    op.drop_index("ix_chat_pets_owner", table_name="chat_pets")
    op.drop_table("chat_pets")
