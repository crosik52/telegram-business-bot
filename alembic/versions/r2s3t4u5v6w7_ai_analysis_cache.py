"""ai_analysis_cache: persist AI analysis results across deploys.

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa

revision = "r2s3t4u5v6w7"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_analysis_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column(
            "analyzed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_analysis_cache_owner_chat",
        "ai_analysis_cache",
        ["owner_id", "chat_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_analysis_cache_owner_chat", table_name="ai_analysis_cache")
    op.drop_table("ai_analysis_cache")
