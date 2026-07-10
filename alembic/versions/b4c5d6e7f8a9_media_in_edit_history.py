"""Add file_id and media_type to message_edit_history

Revision ID: b4c5d6e7f8a9
Revises: f1e2d3c4b5a6
Create Date: 2026-07-10

Each edit-history snapshot now records which media file was attached at
that point in time, enabling accurate notifications and audit trails for
media messages that are later edited or deleted.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "f1e2d3c4b5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "message_edit_history",
        sa.Column("file_id", sa.String(512), nullable=True),
    )
    op.add_column(
        "message_edit_history",
        sa.Column("media_type", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("message_edit_history", "media_type")
    op.drop_column("message_edit_history", "file_id")
