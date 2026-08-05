"""Add revival_count / max_revivals to chat_pets (v5).

Revision ID: z0a1b2c3d4e5
Revises: y9z0a1b2c3d4
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "z0a1b2c3d4e5"
down_revision = "y9z0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_pets",
        sa.Column("revival_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "chat_pets",
        sa.Column("max_revivals", sa.Integer(), nullable=False, server_default="3"),
    )


def downgrade() -> None:
    op.drop_column("chat_pets", "max_revivals")
    op.drop_column("chat_pets", "revival_count")
