"""Add last_fed_by to chat_pets (v6).

Revision ID: a1b2c3d4e5f7
Revises: z0a1b2c3d4e5
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f7"
down_revision = "z0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_pets",
        sa.Column("last_fed_by", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_pets", "last_fed_by")
