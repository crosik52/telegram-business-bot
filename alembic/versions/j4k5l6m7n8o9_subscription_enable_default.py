"""subscription: flip is_enabled default to True and enable existing config row

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-07-10 19:10:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "j4k5l6m7n8o9"
down_revision = "i3j4k5l6m7n8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Change column server default to TRUE
    op.alter_column(
        "subscription_config",
        "is_enabled",
        existing_type=sa.Boolean(),
        server_default=sa.text("true"),
        existing_nullable=False,
    )
    # Enable any already-created config row (e.g. created before this migration)
    op.execute(
        "UPDATE subscription_config SET is_enabled = TRUE WHERE is_enabled = FALSE"
    )


def downgrade() -> None:
    op.alter_column(
        "subscription_config",
        "is_enabled",
        existing_type=sa.Boolean(),
        server_default=sa.text("false"),
        existing_nullable=False,
    )
