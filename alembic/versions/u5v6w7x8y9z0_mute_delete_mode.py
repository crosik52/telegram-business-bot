"""mute_delete_mode: add delete_msgs_until to chat_settings.

When set, the bot auto-deletes every incoming counterpart message until the
timestamp expires or the owner runs !unmute.

Revision ID: u5v6w7x8y9z0
Revises: t4u5v6w7x8y9
Create Date: 2026-07-27
"""

from alembic import op
import sqlalchemy as sa

revision = "u5v6w7x8y9z0"
down_revision = "t4u5v6w7x8y9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_settings",
        sa.Column(
            "delete_msgs_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    try:
        op.drop_column("chat_settings", "delete_msgs_until")
    except Exception:
        pass
