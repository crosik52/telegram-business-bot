"""Upgrade-path test: partial unique index migration blocks duplicate milestones.

This test validates the migration ``o9p0q1r2s3t4`` against a pre-existing
schema that does NOT yet have the ``uq_milestone_reward_per_user`` index.

It simulates a real production upgrade:
1. Create the referral_reward_log table WITHOUT the index (old schema).
2. Pre-populate it with rows including milestone rows (existing data).
3. Apply the migration DDL (``CREATE UNIQUE INDEX … WHERE …``).
4. Attempt to INSERT a duplicate milestone row → must be rejected.
5. Verify that non-milestone rows (per_activation, welcome) with duplicate
   (user_telegram_id, reward_value) are NOT blocked by the partial index.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine


# ---------------------------------------------------------------------------
# Fixture — bare SQLite engine, no ORM metadata
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def bare_engine(tmp_path):
    """Fresh SQLite engine with NO ORM schema — simulates a pre-migration DB."""
    url = f"sqlite+aiosqlite:///{tmp_path}/upgrade_test.db"
    eng = create_async_engine(url, echo=False)
    yield eng
    await eng.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE referral_reward_log (
    id               INTEGER PRIMARY KEY,
    referral_id      INTEGER,
    user_telegram_id INTEGER NOT NULL,
    reward_type      TEXT    NOT NULL,
    reward_value     TEXT    NOT NULL,
    label            TEXT    NOT NULL DEFAULT '',
    granted_at       TEXT    NOT NULL DEFAULT (datetime('now'))
)
"""

_MIGRATION_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_milestone_reward_per_user
ON referral_reward_log (user_telegram_id, reward_value)
WHERE reward_type = 'milestone'
"""

# Full migration DDL: dedup pre-existing rows THEN create index.
# Mirrors the production Alembic migration exactly.
_DEDUP_DDL = """
DELETE FROM referral_reward_log
WHERE reward_type = 'milestone'
  AND id NOT IN (
      SELECT MIN(id)
      FROM referral_reward_log
      WHERE reward_type = 'milestone'
      GROUP BY user_telegram_id, reward_value
  )
"""

_MIGRATION_DDL_FULL = _DEDUP_DDL + ";" + _MIGRATION_DDL

_DROP_INDEX = "DROP INDEX IF EXISTS uq_milestone_reward_per_user"


async def _run(engine, sql, **params):
    """Execute a raw SQL statement."""
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)


async def _run_ddl(engine, ddl):
    async with engine.begin() as conn:
        await conn.execute(text(ddl))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_migration_blocks_duplicate_milestone_after_upgrade(bare_engine):
    """After applying the migration, a duplicate milestone INSERT is rejected."""

    # ── Step 1: create table without index ───────────────────────────────────
    await _run_ddl(bare_engine, _CREATE_TABLE)

    # ── Step 2: insert pre-existing data (simulating production rows) ─────────
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
        "VALUES (:uid, 'milestone', '1', 'milestone 1')",
        uid=1000,
    )
    # Non-milestone row with same reward_value — must remain insertable after migration
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
        "VALUES (:uid, 'per_activation', '7', 'per act 1')",
        uid=1000,
    )

    # ── Step 3: apply migration DDL ───────────────────────────────────────────
    await _run_ddl(bare_engine, _MIGRATION_DDL)

    # ── Step 4: duplicate milestone → must be rejected ───────────────────────
    with pytest.raises((IntegrityError, Exception)) as exc_info:
        await _run(
            bare_engine,
            "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
            "VALUES (:uid, 'milestone', '1', 'milestone 1 duplicate')",
            uid=1000,
        )
    assert "UNIQUE" in str(exc_info.value).upper() or "unique" in str(exc_info.value).lower(), (
        f"Expected a UNIQUE constraint violation but got: {exc_info.value}"
    )


@pytest.mark.asyncio
async def test_migration_does_not_block_non_milestone_duplicates(bare_engine):
    """The partial index must NOT affect welcome or per_activation rows.

    A user can legitimately have multiple per_activation or welcome rows with
    the same reward_value (e.g. two referrals each earning 7 days).
    """
    await _run_ddl(bare_engine, _CREATE_TABLE)
    await _run_ddl(bare_engine, _MIGRATION_DDL)

    # Two per_activation rows with the same (user, reward_value) — must be allowed
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
        "VALUES (:uid, 'per_activation', '7', 'per act 1')",
        uid=2000,
    )
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
        "VALUES (:uid, 'per_activation', '7', 'per act 2')",
        uid=2000,
    )

    # Two welcome rows — must also be allowed (partial index ignores them)
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
        "VALUES (:uid, 'welcome', '3', 'welcome 1')",
        uid=2000,
    )
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
        "VALUES (:uid, 'welcome', '3', 'welcome 2')",
        uid=2000,
    )

    # Verify all 4 rows exist
    async with bare_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT COUNT(*) FROM referral_reward_log WHERE user_telegram_id = :uid"),
            {"uid": 2000},
        )
        count = result.scalar_one()
    assert count == 4, (
        f"Expected 4 non-milestone reward rows to be inserted, got {count}. "
        "The partial index incorrectly blocked non-milestone duplicates."
    )


@pytest.mark.asyncio
async def test_migration_allows_same_milestone_for_different_users(bare_engine):
    """Different users can each have a milestone at the same count."""
    await _run_ddl(bare_engine, _CREATE_TABLE)
    await _run_ddl(bare_engine, _MIGRATION_DDL)

    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
        "VALUES (:uid, 'milestone', '5', 'milestone 5')",
        uid=3001,
    )
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
        "VALUES (:uid, 'milestone', '5', 'milestone 5')",
        uid=3002,
    )

    async with bare_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM referral_reward_log "
                "WHERE reward_type = 'milestone' AND reward_value = '5'"
            )
        )
        count = result.scalar_one()
    assert count == 2, (
        f"Expected 2 milestone rows (one per user), got {count}. "
        "The index incorrectly blocked milestone grants for different users."
    )


@pytest.mark.asyncio
async def test_migration_deduplicates_preexisting_duplicate_milestone_rows(bare_engine):
    """Migration succeeds even when production already has duplicate milestone rows.

    The old concurrent-activation code could grant the same milestone twice
    before either session committed.  This test reproduces that state and
    verifies that the upgrade migration:
      1. Does not raise an error (no failed CREATE UNIQUE INDEX).
      2. Keeps exactly one canonical row per (user_telegram_id, reward_value)
         pair — the earliest-granted one (lowest id).
      3. Still allows distinct milestones and different users.
    """
    await _run_ddl(bare_engine, _CREATE_TABLE)

    # Simulate pre-existing duplicates from the old concurrent-activation race
    # User 5001 has two milestone-1 rows (double-grant race)
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (id, user_telegram_id, reward_type, reward_value, label) "
        "VALUES (10, :uid, 'milestone', '1', 'ms 1 first')",
        uid=5001,
    )
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (id, user_telegram_id, reward_type, reward_value, label) "
        "VALUES (11, :uid, 'milestone', '1', 'ms 1 duplicate')",
        uid=5001,
    )
    # User 5001 also has a non-duplicate milestone-3 row (should survive)
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (id, user_telegram_id, reward_type, reward_value, label) "
        "VALUES (12, :uid, 'milestone', '3', 'ms 3')",
        uid=5001,
    )
    # User 5002 has milestone-1 (a different user — should survive independently)
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (id, user_telegram_id, reward_type, reward_value, label) "
        "VALUES (13, :uid, 'milestone', '1', 'ms 1 user2')",
        uid=5002,
    )
    # A non-milestone row that must be unaffected by dedup
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (id, user_telegram_id, reward_type, reward_value, label) "
        "VALUES (14, :uid, 'per_activation', '7', 'per act')",
        uid=5001,
    )

    # ── Apply migration (dedup + index creation) ──────────────────────────────
    # This must NOT raise — the dedup step handles pre-existing duplicates.
    # Run as two separate statements (SQLite only allows one statement per execute).
    await _run_ddl(bare_engine, _DEDUP_DDL)
    await _run_ddl(bare_engine, _MIGRATION_DDL)

    # ── Verify dedup result ───────────────────────────────────────────────────
    async with bare_engine.connect() as conn:
        # User 5001, milestone 1: exactly one row remains (id=10, the earliest)
        result = await conn.execute(
            text(
                "SELECT id FROM referral_reward_log "
                "WHERE user_telegram_id = :uid AND reward_type = 'milestone' AND reward_value = '1'"
            ),
            {"uid": 5001},
        )
        rows = result.fetchall()
        assert len(rows) == 1, (
            f"Expected 1 milestone-1 row for user 5001 after dedup, got {len(rows)}"
        )
        assert rows[0][0] == 10, (
            f"Expected the earliest row (id=10) to survive, but got id={rows[0][0]}"
        )

        # User 5001 milestone-3 must be untouched
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM referral_reward_log "
                "WHERE user_telegram_id = :uid AND reward_type = 'milestone' AND reward_value = '3'"
            ),
            {"uid": 5001},
        )
        assert result.scalar_one() == 1, "milestone-3 row for user 5001 must survive"

        # User 5002 milestone-1 must be untouched (different user)
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM referral_reward_log "
                "WHERE user_telegram_id = :uid AND reward_type = 'milestone' AND reward_value = '1'"
            ),
            {"uid": 5002},
        )
        assert result.scalar_one() == 1, "milestone-1 row for user 5002 must survive"

        # Non-milestone row must be untouched
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM referral_reward_log "
                "WHERE user_telegram_id = :uid AND reward_type = 'per_activation'"
            ),
            {"uid": 5001},
        )
        assert result.scalar_one() == 1, "per_activation row must not be touched by dedup"

    # ── Verify index now blocks further duplicates ────────────────────────────
    with pytest.raises(Exception) as exc_info:
        await _run(
            bare_engine,
            "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
            "VALUES (:uid, 'milestone', '1', 'ms 1 new duplicate')",
            uid=5001,
        )
    assert "unique" in str(exc_info.value).lower() or "UNIQUE" in str(exc_info.value), (
        f"Expected a UNIQUE constraint violation but got: {exc_info.value}"
    )


@pytest.mark.asyncio
async def test_downgrade_removes_index(bare_engine):
    """Downgrade DDL removes the index; duplicates are accepted again."""
    await _run_ddl(bare_engine, _CREATE_TABLE)
    await _run_ddl(bare_engine, _MIGRATION_DDL)
    await _run_ddl(bare_engine, _DROP_INDEX)

    # After downgrade, duplicate milestone should be insertable
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
        "VALUES (:uid, 'milestone', '1', 'ms 1')",
        uid=4000,
    )
    await _run(
        bare_engine,
        "INSERT INTO referral_reward_log (user_telegram_id, reward_type, reward_value, label) "
        "VALUES (:uid, 'milestone', '1', 'ms 1 dup')",
        uid=4000,
    )

    async with bare_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT COUNT(*) FROM referral_reward_log "
                "WHERE user_telegram_id = :uid AND reward_type = 'milestone'",
            ),
            {"uid": 4000},
        )
        count = result.scalar_one()
    assert count == 2, (
        f"After downgrade the constraint should be gone; expected 2 rows, got {count}."
    )
