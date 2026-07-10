"""Add composite index (business_connection_id, sent_at) on messages

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-07-10 07:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_messages_conn_sent",
        "messages",
        ["business_connection_id", "sent_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_messages_conn_sent", table_name="messages")
