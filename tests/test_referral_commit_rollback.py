"""Integration test: referral activation is atomic — a commit failure leaves no trace.

`try_activate()` only uses `flush()` internally; the actual `commit()` is the
caller's responsibility (see routes.py).  If that commit raises, SQLAlchemy
rolls back the entire transaction.  This test confirms that after such a
rollback:

  1. The referral row is still "pending" (not "active").
  2. No ReferralRewardLog rows exist (neither the referee welcome reward
     nor the referrer per-activation reward were persisted).
  3. No UserSubscription rows exist (the premium grant is also rolled back).

Strategy
--------
We use a real in-memory SQLite database (same pattern as
test_relationship_repository.py) so we exercise actual SQLAlchemy
transaction semantics rather than mocked objects.  To simulate a commit
failure we simply call `session.rollback()` after `try_activate()` succeeds
instead of `commit()`, which is exactly what SQLAlchemy would do internally
when a commit raises.
"""
from __future__ import annotations

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.referral import Referral, ReferralConfig, ReferralRewardLog
from app.models.subscription import UserSubscription
from app.repositories.referral_repository import ReferralRepository

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REFERRER_ID = 1_111_111
REFERRED_ID = 2_222_222

DATABASE_URL = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def engine():
    """Fresh in-memory SQLite engine with all tables created."""
    eng = create_async_engine(DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_config(session: AsyncSession, referee_days: int = 3, referrer_days: int = 7) -> ReferralConfig:
    """Insert a minimal ReferralConfig with no milestones (to keep tests simple)."""
    cfg = ReferralConfig(
        is_enabled=True,
        referee_reward_days=referee_days,
        referrer_reward_days=referrer_days,
        milestones=[],       # skip milestone branch
        levels=[{"name": "Bronze", "min": 0, "emoji": "🥉", "color": "#CD7F32"}],
    )
    session.add(cfg)
    await session.flush()
    return cfg


async def _seed_pending_referral(session: AsyncSession) -> Referral:
    """Insert a pending Referral row."""
    ref = Referral(
        referrer_telegram_id=REFERRER_ID,
        referred_telegram_id=REFERRED_ID,
        status="pending",
        referred_first_name="Test",
        referred_username="testuser",
    )
    session.add(ref)
    await session.flush()
    return ref


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_leaves_referral_pending(session_factory):
    """After a simulated commit failure, the referral row stays 'pending'."""

    # ── Phase 1: seed data in its own committed transaction ──────────────────
    async with session_factory() as seed_session:
        await _seed_config(seed_session)
        await _seed_pending_referral(seed_session)
        await seed_session.commit()

    # ── Phase 2: activate inside a transaction, then roll back ───────────────
    async with session_factory() as work_session:
        repo = ReferralRepository(work_session)
        ref, rewards = await repo.try_activate(REFERRED_ID, has_business_connection=True)

        # try_activate must have detected the pending referral and returned rewards
        assert ref is not None, "try_activate returned None — no pending referral found"
        assert rewards, "try_activate returned no rewards — config or seeding error"

        # Simulate commit failure: roll back instead of committing
        await work_session.rollback()

    # ── Phase 3: inspect DB in a fresh session ───────────────────────────────
    async with session_factory() as check_session:
        # 1. Referral must still be "pending"
        result = await check_session.execute(
            select(Referral).where(Referral.referred_telegram_id == REFERRED_ID)
        )
        referral_row = result.scalar_one_or_none()
        assert referral_row is not None, "Referral row disappeared — unexpected"
        assert referral_row.status == "pending", (
            f"Expected status='pending' but got {referral_row.status!r}. "
            "The activation was not rolled back."
        )
        assert referral_row.activated_at is None, (
            "activated_at was persisted despite the rollback."
        )

        # 2. No ReferralRewardLog rows must exist
        log_result = await check_session.execute(select(ReferralRewardLog))
        logs = log_result.scalars().all()
        assert logs == [], (
            f"Expected no reward logs after rollback, found {len(logs)}: "
            + str([(r.reward_type, r.user_telegram_id) for r in logs])
        )

        # 3. No UserSubscription rows must exist (premium grants are also rolled back)
        sub_result = await check_session.execute(select(UserSubscription))
        subs = sub_result.scalars().all()
        assert subs == [], (
            f"Expected no subscriptions after rollback, found {len(subs)}: "
            + str([(s.user_telegram_id, s.expires_at) for s in subs])
        )


@pytest.mark.asyncio
async def test_rollback_clears_both_reward_sides(session_factory):
    """Both the referred user's welcome reward and the referrer's per-activation
    reward are rolled back when the commit fails.

    This test is more explicit: it verifies neither user's reward is persisted.
    """

    async with session_factory() as seed_session:
        await _seed_config(seed_session, referee_days=3, referrer_days=7)
        await _seed_pending_referral(seed_session)
        await seed_session.commit()

    async with session_factory() as work_session:
        repo = ReferralRepository(work_session)
        ref, rewards = await repo.try_activate(REFERRED_ID, has_business_connection=True)

        assert ref is not None
        # Both reward sides must have been produced before the rollback
        reward_types = {r["type"] for r in rewards}
        assert "welcome" in reward_types, (
            "Expected a 'welcome' reward for the referred user."
        )
        assert "per_activation" in reward_types, (
            "Expected a 'per_activation' reward for the referrer."
        )

        # Simulate commit failure
        await work_session.rollback()

    async with session_factory() as check_session:
        # Confirm neither user has a reward log entry
        for uid, label in [(REFERRED_ID, "referred"), (REFERRER_ID, "referrer")]:
            result = await check_session.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.user_telegram_id == uid
                )
            )
            user_logs = result.scalars().all()
            assert user_logs == [], (
                f"Expected no reward log for {label} (id={uid}) after rollback, "
                f"found {len(user_logs)}."
            )

        # Confirm neither user has a subscription
        for uid, label in [(REFERRED_ID, "referred"), (REFERRER_ID, "referrer")]:
            result = await check_session.execute(
                select(UserSubscription).where(
                    UserSubscription.user_telegram_id == uid
                )
            )
            user_subs = result.scalars().all()
            assert user_subs == [], (
                f"Expected no subscription for {label} (id={uid}) after rollback, "
                f"found {len(user_subs)}."
            )


@pytest.mark.asyncio
async def test_successful_commit_activates_correctly(session_factory):
    """Control case: when the commit succeeds, the referral is activated and
    reward logs are persisted.  This ensures the rollback tests are not
    vacuously passing because try_activate itself does nothing."""

    async with session_factory() as seed_session:
        await _seed_config(seed_session, referee_days=3, referrer_days=7)
        await _seed_pending_referral(seed_session)
        await seed_session.commit()

    async with session_factory() as work_session:
        repo = ReferralRepository(work_session)
        ref, rewards = await repo.try_activate(REFERRED_ID, has_business_connection=True)
        assert ref is not None
        assert rewards
        await work_session.commit()   # ← real commit this time

    async with session_factory() as check_session:
        # Referral must now be "active"
        result = await check_session.execute(
            select(Referral).where(Referral.referred_telegram_id == REFERRED_ID)
        )
        referral_row = result.scalar_one_or_none()
        assert referral_row is not None
        assert referral_row.status == "active", (
            f"Expected status='active' after a successful commit, got {referral_row.status!r}."
        )
        assert referral_row.activated_at is not None, (
            "activated_at must be set after successful activation."
        )

        # Reward logs must exist
        log_result = await check_session.execute(select(ReferralRewardLog))
        logs = log_result.scalars().all()
        assert len(logs) >= 2, (
            f"Expected at least 2 reward logs (welcome + per_activation), got {len(logs)}."
        )

        # Subscriptions must exist for both users
        for uid, label in [(REFERRED_ID, "referred"), (REFERRER_ID, "referrer")]:
            result = await check_session.execute(
                select(UserSubscription).where(
                    UserSubscription.user_telegram_id == uid
                )
            )
            user_subs = result.scalars().all()
            assert user_subs, (
                f"Expected a subscription for {label} (id={uid}) after successful commit."
            )
