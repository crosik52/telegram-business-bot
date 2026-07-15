"""wallet: add CHECK constraint balance >= 0

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-07-15
"""

from alembic import op

revision = "m7n8o9p0q1r2"
down_revision = "l6m7n8o9p0q1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOT VALID defers row-level validation so the constraint is added without
    # a full table scan, avoiding downtime on large tables.  The constraint
    # will be enforced for all new writes immediately.
    op.execute(
        "ALTER TABLE user_wallets "
        "ADD CONSTRAINT chk_balance_non_negative "
        "CHECK (balance >= 0) NOT VALID"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE user_wallets "
        "DROP CONSTRAINT IF EXISTS chk_balance_non_negative"
    )
