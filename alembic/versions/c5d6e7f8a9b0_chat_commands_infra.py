"""Add chat_settings and contact_notes tables

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-07-10

Infrastructure for per-chat mute settings and owner-created contact notes,
both managed via !command messages in business chats.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | None = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("business_connection_id", sa.String(255), nullable=False),
        sa.Column("chat_id", sa.BigInteger, nullable=False),
        sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "business_connection_id", "chat_id", name="uq_chat_settings"
        ),
    )
    op.create_index(
        "ix_chat_settings_business_connection_id",
        "chat_settings",
        ["business_connection_id"],
    )

    op.create_table(
        "contact_notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("business_connection_id", sa.String(255), nullable=False),
        sa.Column("chat_id", sa.BigInteger, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_contact_notes_business_connection_id",
        "contact_notes",
        ["business_connection_id"],
    )
    op.create_index(
        "ix_contact_notes_chat_id",
        "contact_notes",
        ["chat_id"],
    )


def downgrade() -> None:
    op.drop_table("contact_notes")
    op.drop_table("chat_settings")
