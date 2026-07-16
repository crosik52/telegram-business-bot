"""Integration test: milestone sweep retries correctly after a server restart mid-batch.

Scenario
--------
The background ``_milestone_sweep_loop`` guarantees at-least-once milestone
evaluation by relying on ``milestone_checked=False`` to identify referrals
whose Phase-2 evaluation was interrupted (e.g. the server restarted after
Phase 1 committed but before ``evaluate_and_grant_milestones`` could run).

This test file verifies the full retry path:

1. ``test_unchecked_after_phase1_without_phase2``
   Phase 1 (try_activate) is committed; Phase 2 is intentionally skipped.
   Asserts:
   - ``milestone_checked`` is still ``False`` on the referral row.
   - ``list_unchecked_referral_ids`` returns the referral.

2. ``test_sweep_grants_milestone_and_marks_checked``
   After the crash-simulation above, ``evaluate_and_grant_milestones`` is
   called in a fresh session (exactly as the background sweep does).
   Asserts:
   - The milestone ``ReferralRewardLog`` row is inserted.
   - ``milestone_checked`` is flipped to ``True``.
   - A second sweep call (simulate the loop running again) produces no
     additional milestone grant — the idempotency guard holds.

3. ``test_sweep_skips_already_checked_referral``
   Referral that went through both Phase 1 and Phase 2 normally is NOT
   returned by ``list_unchecked_referral_ids``.

Strategy
--------
File-based SQLite (aiosqlite) so separate sessions share committed state,
mirroring the real runtime behaviour of the background sweep loop.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.referral import Referral, ReferralConfig, ReferralRewardLog
from app.models.subscription import UserSubscription
from app.repositories.referral_repository import ReferralRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def engine(tmp_path):
    """File-based SQLite so independent sessions share committed state."""
    url = f"sqlite+aiosqlite:///{tmp_path}/sweep_retry.db"
    eng = create_async_engine(url, echo=False)
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


REFERRER_ID = 7_000_001
REFERRED_ID = 7_000_002
MILESTONE_COUNT = 1
MILESTONE_DAYS = 14


async def _seed_db(session: AsyncSession) -> Referral:
    """Insert a ReferralConfig with one milestone and a pending Referral row."""
    cfg = ReferralConfig(
        is_enabled=True,
        referrer_reward_days=7,
        referee_reward_days=3,
        milestones=[
            {
                "count": MILESTONE_COUNT,
                "type": "premium_days",
                "value": MILESTONE_DAYS,
                "label": f"+{MILESTONE_DAYS} дн. Premium (milestone {MILESTONE_COUNT})",
            }
        ],
        levels=[{"name": "Bronze", "min": 0, "emoji": "🥉", "color": "#CD7F32"}],
    )
    session.add(cfg)

    ref = Referral(
        referrer_telegram_id=REFERRER_ID,
        referred_telegram_id=REFERRED_ID,
        status="pending",
        referred_first_name="SweepUser",
        referred_username="sweepuser",
    )
    session.add(ref)
    await session.commit()
    return ref


async def _phase1_activate_and_commit(session_factory) -> Referral:
    """Run Phase 1 (try_activate) in its own session and commit.

    Phase 2 is intentionally NOT called — simulating a server crash between
    Phase 1 commit and Phase 2 execution.
    """
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ref, rewards = await repo.try_activate(
            referred_telegram_id=REFERRED_ID,
            has_business_connection=True,
        )
        assert ref is not None, "try_activate returned None — seeding error"
        assert rewards, "try_activate returned no base rewards — config error"
        await sess.commit()
        return ref


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unchecked_after_phase1_without_phase2(session_factory):
    """After Phase 1 commit with no Phase 2, milestone_checked stays False
    and list_unchecked_referral_ids returns the referral.

    This is the exact state left behind by a server crash mid-batch: the
    referral is active (Phase 1 succeeded) but milestone_checked is still
    False because Phase 2 never ran.
    """
    # Seed
    async with session_factory() as seed:
        await _seed_db(seed)

    # Phase 1 only — no Phase 2
    ref = await _phase1_activate_and_commit(session_factory)

    # Inspect the referral row in a fresh session
    async with session_factory() as check:
        row = (
            await check.execute(
                select(Referral).where(Referral.id == ref.id)
            )
        ).scalar_one_or_none()

        assert row is not None, "Referral row not found after Phase 1 commit"
        assert row.status == "active", (
            f"Expected status='active' after Phase 1, got {row.status!r}"
        )
        assert row.milestone_checked is False, (
            "milestone_checked must remain False when Phase 2 was skipped — "
            "the sweep relies on this flag to detect interrupted evaluations."
        )

    # list_unchecked_referral_ids must include this referral
    async with session_factory() as sweep_session:
        repo = ReferralRepository(sweep_session)
        unchecked = await repo.list_unchecked_referral_ids(limit=50)

    assert len(unchecked) >= 1, (
        "list_unchecked_referral_ids returned an empty list — the referral "
        "with milestone_checked=False was not found. The sweep would never "
        "retry this referral."
    )
    unchecked_ids = [row[0] for row in unchecked]
    assert ref.id in unchecked_ids, (
        f"Referral id={ref.id} not in unchecked list {unchecked_ids}. "
        "The sweep query is filtering it out incorrectly."
    )
    unchecked_referrer_ids = {row[1] for row in unchecked}
    assert REFERRER_ID in unchecked_referrer_ids, (
        "referrer_telegram_id not returned correctly from list_unchecked_referral_ids."
    )


@pytest.mark.asyncio
async def test_sweep_grants_milestone_and_marks_checked(session_factory):
    """The sweep (evaluate_and_grant_milestones) correctly retries Phase 2
    after a simulated server restart:

    - Milestone ReferralRewardLog is inserted.
    - milestone_checked is set to True.
    - A second sweep call produces no duplicate grant (idempotent).
    """
    # Seed
    async with session_factory() as seed:
        await _seed_db(seed)

    # Phase 1 only — crash before Phase 2
    ref = await _phase1_activate_and_commit(session_factory)

    # Sweep Phase 2: exactly what _milestone_sweep_loop does per referral
    async with session_factory() as sweep_sess:
        repo = ReferralRepository(sweep_sess)
        rewards = await repo.evaluate_and_grant_milestones(REFERRER_ID, ref.id)
        await sweep_sess.commit()

    # At least one milestone reward must have been produced
    assert any(r["type"] == "milestone" for r in rewards), (
        "evaluate_and_grant_milestones produced no milestone reward — "
        "the sweep did not grant the milestone despite milestone_checked=False. "
        f"Rewards returned: {rewards}"
    )

    # Verify milestone log in DB
    async with session_factory() as check:
        milestone_logs = (
            await check.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.user_telegram_id == REFERRER_ID,
                    ReferralRewardLog.reward_type == "milestone",
                    ReferralRewardLog.reward_value == str(MILESTONE_COUNT),
                )
            )
        ).scalars().all()

        assert len(milestone_logs) == 1, (
            f"Expected exactly 1 milestone reward log after sweep, "
            f"found {len(milestone_logs)}."
        )

        # milestone_checked must now be True
        row = (
            await check.execute(
                select(Referral).where(Referral.id == ref.id)
            )
        ).scalar_one_or_none()

        assert row is not None
        assert row.milestone_checked is True, (
            "milestone_checked must be True after evaluate_and_grant_milestones "
            "commits — the sweep loop will otherwise re-process this referral "
            "on every cycle."
        )

    # Second sweep call: idempotency — no additional milestone grant
    async with session_factory() as sweep_sess2:
        repo2 = ReferralRepository(sweep_sess2)
        rewards2 = await repo2.evaluate_and_grant_milestones(REFERRER_ID, ref.id)
        await sweep_sess2.commit()

    milestone_rewards2 = [r for r in rewards2 if r["type"] == "milestone"]
    assert milestone_rewards2 == [], (
        f"Second sweep call produced extra milestone grants: {milestone_rewards2}. "
        "The duplicate-guard (unique index + savepoint) failed to prevent a re-grant."
    )

    # Still exactly 1 milestone log after the second sweep
    async with session_factory() as check2:
        all_milestone_logs = (
            await check2.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.user_telegram_id == REFERRER_ID,
                    ReferralRewardLog.reward_type == "milestone",
                )
            )
        ).scalars().all()

        assert len(all_milestone_logs) == 1, (
            f"Expected exactly 1 milestone log after two sweep calls, "
            f"found {len(all_milestone_logs)}. Double-grant detected."
        )


@pytest.mark.asyncio
async def test_sweep_skips_already_checked_referral(session_factory):
    """A referral that completed both Phase 1 and Phase 2 normally is NOT
    returned by list_unchecked_referral_ids.

    Ensures the sweep does not re-process referrals that were already handled
    on the happy path.
    """
    # Seed
    async with session_factory() as seed:
        await _seed_db(seed)

    # Phase 1
    ref = await _phase1_activate_and_commit(session_factory)

    # Phase 2 — normal completion
    async with session_factory() as phase2_sess:
        repo = ReferralRepository(phase2_sess)
        await repo.evaluate_and_grant_milestones(REFERRER_ID, ref.id)
        await phase2_sess.commit()

    # Sweep query must return empty — this referral is already checked
    async with session_factory() as sweep_sess:
        repo = ReferralRepository(sweep_sess)
        unchecked = await repo.list_unchecked_referral_ids(limit=50)

    unchecked_ids = [row[0] for row in unchecked]
    assert ref.id not in unchecked_ids, (
        f"Referral id={ref.id} appeared in unchecked list after Phase 2 completed. "
        "milestone_checked was not set to True, or list_unchecked_referral_ids "
        "is not filtering correctly."
    )
