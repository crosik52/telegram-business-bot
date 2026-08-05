"""Add last_played_by / last_cuddled_by to chat_pets (v7).

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_pets", sa.Column("last_played_by",  sa.BigInteger(), nullable=True))
    op.add_column("chat_pets", sa.Column("last_cuddled_by", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_pets", "last_cuddled_by")
    op.drop_column("chat_pets", "last_played_by")
