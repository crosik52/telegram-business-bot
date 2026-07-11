"""media_cache: store downloaded file bytes for self-destructing media

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-07-11 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "k5l6m7n8o9p0"
down_revision = "j4k5l6m7n8o9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_unique_id", sa.String(255), nullable=False),
        sa.Column("file_id", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("file_data", sa.LargeBinary(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_media_cache_file_unique_id",
        "media_cache",
        ["file_unique_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_media_cache_file_unique_id", table_name="media_cache")
    op.drop_table("media_cache")
