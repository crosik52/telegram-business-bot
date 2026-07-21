"""analysis_result_cache: add analysis_results table for persistent AI cache.

Stores Gemini analysis results per (owner_id, chat_id) so results survive
deploys and avoid re-calling the API within the 24-hour TTL window.

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
        "analysis_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("stored_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_results_owner_id", "analysis_results", ["owner_id"]
    )
    op.create_index(
        "ix_analysis_results_chat_id", "analysis_results", ["chat_id"]
    )
    op.create_index(
        "ix_analysis_results_owner_chat",
        "analysis_results",
        ["owner_id", "chat_id"],
        unique=True,
    )


def downgrade() -> None:
    try:
        op.drop_index("ix_analysis_results_owner_chat", table_name="analysis_results")
        op.drop_index("ix_analysis_results_chat_id", table_name="analysis_results")
        op.drop_index("ix_analysis_results_owner_id", table_name="analysis_results")
        op.drop_table("analysis_results")
    except Exception:
        pass
