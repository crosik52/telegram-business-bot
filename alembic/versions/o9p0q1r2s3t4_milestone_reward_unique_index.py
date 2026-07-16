"""milestone reward unique index: partial unique index on referral_reward_log
for milestone deduplication under concurrent activations.

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-07-16
"""

from alembic import op

revision = "o9p0q1r2s3t4"
down_revision = "n8o9p0q1r2s3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create a partial unique index covering only milestone reward rows.

    The index guarantees that each (user_telegram_id, reward_value) pair can
    appear at most once in rows where reward_type = 'milestone'.  This is the
    DB-level guard that makes ``evaluate_and_grant_milestones`` idempotent
    under concurrent calls: the second INSERT raises an IntegrityError caught
    inside a savepoint, so exactly one milestone grant persists even when two
    sessions race to insert the same row.

    Both SQLite (≥3.8.9) and PostgreSQL support the WHERE-filtered unique
    index syntax used here.

    Pre-existing duplicate deduplication
    -------------------------------------
    Under the old concurrent-activation code, two sessions could both grant
    the same milestone before either committed, leaving duplicate rows.  We
    DELETE the extras before creating the index so the migration never fails on
    a production DB that was already exposed to that race.

    Dedup strategy: keep the row with the lowest ``id`` (earliest grant) for
    each (user_telegram_id, reward_value) pair where reward_type='milestone'.
    This is deterministic and idempotent — running it twice produces the same
    result.
    """
    # Step 1: remove duplicate milestone rows, keeping the earliest-granted one.
    # SQLite and PostgreSQL both support this DELETE … WHERE id NOT IN (subquery)
    # pattern.
    op.execute(
        """
        DELETE FROM referral_reward_log
        WHERE reward_type = 'milestone'
          AND id NOT IN (
              SELECT MIN(id)
              FROM referral_reward_log
              WHERE reward_type = 'milestone'
              GROUP BY user_telegram_id, reward_value
          )
        """
    )

    # Step 2: now that duplicates are gone, create the unique index safely.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_milestone_reward_per_user
        ON referral_reward_log (user_telegram_id, reward_value)
        WHERE reward_type = 'milestone'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_milestone_reward_per_user")
