"""Add relationships.category for friendship vs romantic distinction.

Revision ID: x8y9z0a1b2c3
Revises: w7x8y9z0a1b2
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "x8y9z0a1b2c3"
down_revision = "w7x8y9z0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add category column — "friendship" (no romantic progression) or "romantic"
    op.add_column(
        "relationships",
        sa.Column(
            "category",
            sa.String(20),
            nullable=False,
            server_default="romantic",
        ),
    )


def downgrade() -> None:
    op.drop_column("relationships", "category")
