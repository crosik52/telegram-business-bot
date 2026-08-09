"""Create casino_wins table for the daily/weekly biggest-wins leaderboard.

Revision ID: b3c4d5e6f7a8
Revises: z0a1b2c3d4e5
Create Date: 2026-08-09
"""
from alembic import op
import sqlalchemy as sa

revision = "b3c4d5e6f7a8"
down_revision = "z0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "casino_wins",
        sa.Column("id",     sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("uid",    sa.BigInteger(), nullable=False),
        sa.Column("name",   sa.String(30),   nullable=False),
        sa.Column("game",   sa.String(10),   nullable=False),
        sa.Column("bet",    sa.Integer(),    nullable=False),
        sa.Column("payout", sa.Integer(),    nullable=False),
        sa.Column("net",    sa.Integer(),    nullable=False),
        sa.Column("mult",   sa.Float(),      nullable=False),
        sa.Column("ts",     sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_casino_wins_uid",    "casino_wins", ["uid"])
    op.create_index("ix_casino_wins_ts",     "casino_wins", ["ts"])
    op.create_index("ix_casino_wins_ts_net", "casino_wins", ["ts", "net"])


def downgrade() -> None:
    op.drop_index("ix_casino_wins_ts_net", table_name="casino_wins")
    op.drop_index("ix_casino_wins_ts",     table_name="casino_wins")
    op.drop_index("ix_casino_wins_uid",    table_name="casino_wins")
    op.drop_table("casino_wins")
