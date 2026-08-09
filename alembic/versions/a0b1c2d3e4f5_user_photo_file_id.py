"""Add photo_file_id to telegram_users

Revision ID: a0b1c2d3e4f5
Revises: z0a1b2c3d4e5
Create Date: 2026-08-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "a0b1c2d3e4f5"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telegram_users",
        sa.Column("photo_file_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telegram_users", "photo_file_id")
