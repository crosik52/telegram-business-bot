"""Add relationships.meta JSON blob for streaks/quests/anniversaries.

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa

revision = "w7x8y9z0a1b2"
down_revision = "v6w7x8y9z0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("relationships", sa.Column("meta", sa.Text(), nullable=True))
    # Backfill levels for the 5→10 max-level raise: rows whose XP already
    # exceeded the old cap keep advancing instead of being stuck at level 5.
    op.execute(
        "UPDATE relationships SET level = LEAST(10, xp / 200 + 1) "
        "WHERE xp / 200 + 1 > level"
    )


def downgrade() -> None:
    op.drop_column("relationships", "meta")
