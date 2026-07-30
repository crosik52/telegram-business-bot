"""messages_trgm_index: GIN trigram indexes on messages.text and messages.caption.

Enables fast ILIKE '%query%' search on the messages table without a full
sequential scan.  Requires the pg_trgm extension (created here if absent).

NOTE: index creation runs inside Alembic's normal transaction.  CONCURRENTLY
is intentionally omitted because PostgreSQL forbids it inside a transaction
block.  The migration will briefly hold an ACCESS SHARE lock while building
the index; this is acceptable during a planned deploy.

Revision ID: v6w7x8y9z0a1
Revises: u5v6w7x8y9z0
Create Date: 2026-07-30
"""

from alembic import op

revision = "v6w7x8y9z0a1"
down_revision = "u5v6w7x8y9z0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable trigram extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # GIN trigram index on messages.text
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_messages_text_trgm "
        "ON messages USING GIN (text gin_trgm_ops)"
    )

    # GIN trigram index on messages.caption
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_messages_caption_trgm "
        "ON messages USING GIN (caption gin_trgm_ops)"
    )


def downgrade() -> None:
    try:
        op.execute("DROP INDEX IF EXISTS ix_messages_caption_trgm")
    except Exception:
        pass
    try:
        op.execute("DROP INDEX IF EXISTS ix_messages_text_trgm")
    except Exception:
        pass
