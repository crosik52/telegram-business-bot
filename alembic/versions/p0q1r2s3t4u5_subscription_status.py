"""subscription status column: add status TEXT to user_subscriptions.

Adds an explicit lifecycle column so the admin panel can show whether each
subscription is 'active', 'paused', 'cancelled', or 'refunded' — and so
_grant_premium can filter precisely on status='active' rather than relying
solely on is_active.

Migration contract
------------------
* Existing rows where is_active=True  → status='active'
* Existing rows where is_active=False → status='cancelled'
* DEFAULT 'active' covers any future INSERT that omits the column.

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-07-16
"""

from alembic import op

revision = "p0q1r2s3t4u5"
down_revision = "o9p0q1r2s3t4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add status column and backfill from is_active."""
    from alembic import op as _op
    from sqlalchemy import text

    bind = op.get_bind()
    dialect = bind.dialect.name

    # Add column with a default so every existing row gets 'active'.
    op.execute(
        "ALTER TABLE user_subscriptions "
        "ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
    )

    # Backfill: rows that were already deactivated become 'cancelled'.
    # Use the correct boolean literal for each dialect.
    if dialect == "sqlite":
        op.execute(
            "UPDATE user_subscriptions SET status = 'cancelled' WHERE is_active = 0"
        )
    else:
        # PostgreSQL and others: boolean FALSE
        op.execute(
            "UPDATE user_subscriptions SET status = 'cancelled' WHERE is_active = FALSE"
        )


def downgrade() -> None:
    # SQLite does not support DROP COLUMN in older versions; skip gracefully.
    try:
        op.execute("ALTER TABLE user_subscriptions DROP COLUMN status")
    except Exception:
        pass
