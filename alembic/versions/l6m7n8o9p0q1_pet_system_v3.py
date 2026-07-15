"""pet_system_v3 — add upgrades column to chat_pets

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa

revision = "l6m7n8o9p0q1"
down_revision = "k5l6m7n8o9p0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_pets",
        sa.Column("upgrades", sa.String(length=400), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_pets", "upgrades")
