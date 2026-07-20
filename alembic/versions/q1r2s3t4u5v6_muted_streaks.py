"""user_settings: add muted_streaks JSON column.

Stores a list of chat_ids for which the owner has silenced streak notifications.

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-07-20
"""

from alembic import op

revision = "q1r2s3t4u5v6"
down_revision = "p0q1r2s3t4u5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_settings ADD COLUMN muted_streaks JSON"
    )


def downgrade() -> None:
    try:
        op.execute("ALTER TABLE user_settings DROP COLUMN muted_streaks")
    except Exception:
        pass
