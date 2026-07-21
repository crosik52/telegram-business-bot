"""ai_daily_count: persist per-user daily AI analysis usage across deploys.

The previous in-memory _DAILY_COUNTS dict reset on every redeploy, allowing
users to exceed their daily limit. This table is the source of truth.

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa

revision = "t4u5v6w7x8y9"
down_revision = "s3t4u5v6w7x8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_analysis_daily_counts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("count", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_analysis_daily_counts_owner_date",
        "ai_analysis_daily_counts",
        ["owner_id", "date"],
        unique=True,
    )


def downgrade() -> None:
    try:
        op.drop_index(
            "ix_ai_analysis_daily_counts_owner_date",
            table_name="ai_analysis_daily_counts",
        )
        op.drop_table("ai_analysis_daily_counts")
    except Exception:
        pass
