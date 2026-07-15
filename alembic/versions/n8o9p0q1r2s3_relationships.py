"""relationships: add relationship system table

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op

revision = "n8o9p0q1r2s3"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relationships",
        sa.Column("id",           sa.Integer(),              nullable=False),
        sa.Column("user_a_id",    sa.BigInteger(),           nullable=False),
        sa.Column("user_b_id",    sa.BigInteger(),           nullable=False),
        sa.Column("initiator_id", sa.BigInteger(),           nullable=False),
        sa.Column("rel_type",     sa.String(20),             nullable=False, server_default="friends"),
        sa.Column("level",        sa.Integer(),              nullable=False, server_default="1"),
        sa.Column("xp",           sa.Integer(),              nullable=False, server_default="0"),
        sa.Column("status",       sa.String(20),             nullable=False, server_default="pending"),
        sa.Column("created_at",   sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_gift_a",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_gift_b",  sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_a_id", "user_b_id", name="uq_relationship_pair"),
    )
    op.create_index("ix_relationships_user_a_id", "relationships", ["user_a_id"])
    op.create_index("ix_relationships_user_b_id", "relationships", ["user_b_id"])
    op.create_index("ix_relationships_status",    "relationships", ["status"])
    op.create_index("ix_relationships_rel_type",  "relationships", ["rel_type"])


def downgrade() -> None:
    op.drop_table("relationships")
