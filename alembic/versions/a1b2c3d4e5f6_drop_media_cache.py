"""drop media_cache table (dead code — Telegram Business API never exposes view-once media)

Revision ID: a1b2c3d4e5f6_drop_media_cache
Revises: z0a1b2c3d4e5
Create Date: 2026-08-23 00:00:00.000000
"""

from alembic import op

revision = "a1b2c3d4e5f6_drop_media_cache"
down_revision = "z0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_media_cache_file_unique_id", table_name="media_cache", if_exists=True)
    op.drop_table("media_cache", if_exists=True)


def downgrade() -> None:
    # Restoring the table is intentionally omitted: the table was dead code and
    # should never be re-created in production.  Downgrade is a no-op.
    pass
