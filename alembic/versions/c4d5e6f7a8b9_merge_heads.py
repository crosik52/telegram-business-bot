"""Merge photo-file-id branch with casino-wins branch into a single head.

Revision ID: c4d5e6f7a8b9
Revises: a0b1c2d3e4f5, b3c4d5e6f7a8
Create Date: 2026-08-09
"""
from alembic import op

revision = "c4d5e6f7a8b9"
down_revision = ("a0b1c2d3e4f5", "b3c4d5e6f7a8")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass  # merge only — no schema changes


def downgrade() -> None:
    pass
