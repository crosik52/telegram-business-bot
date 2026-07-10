"""chat_pets: partial unique index on (owner_telegram_id, chat_id) WHERE is_alive

Prevents duplicate alive pets per owner+chat pair at the DB level,
making adopt() race-safe when combined with IntegrityError handling.

Revision ID: 9a8b7c6d5e4f
Revises: f2a3b4c5d6e7
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op

revision = "9a8b7c6d5e4f"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNIQUE INDEX uq_chat_pets_alive
        ON chat_pets (owner_telegram_id, chat_id)
        WHERE is_alive = true
        """
    )


def downgrade() -> None:
    op.drop_index("uq_chat_pets_alive", table_name="chat_pets")
