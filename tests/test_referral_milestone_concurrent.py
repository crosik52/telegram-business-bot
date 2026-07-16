"""Concurrency test: milestone reward fires exactly once when two different
referred users activate simultaneously for the same referrer.

The TOCTOU window (now fixed)
------------------------------
The old inline milestone evaluation inside ``try_activate`` worked like:

    active_count = await self._count_active(referrer_id)   # READ (stale snapshot)
    for milestone in cfg.milestones:
        if milestone["count"] == active_count:
            grant()

When two referrals (referred_A and referred_B, both pointing at the same
referrer) activate concurrently:

    Session A: flush referred_A → active  (not yet committed)
    Session B: flush referred_B → active  (not yet committed)
    Session A: _count_active → 1           (B not committed yet) → skip count=2
    Session B: _count_active → 1           (A not committed yet) → skip count=2
    Both commit → active_count=2, milestone NEVER granted (skip race).

The fix
-------
Milestone evaluation is split into a mandatory two-phase flow:

    Phase 1 — try_activate() → commit    (welcome + per-activation rewards only)
    Phase 2 — evaluate_and_grant_milestones() → commit (milestones only)

After Phase 1 commits, Phase 2 calls ``_count_active`` which now reads all
committed rows, including concurrent activations that finished first.  Using
``<=`` (instead of ``==``) guarantees every crossed threshold is checked.  The
partial unique index ``uq_milestone_reward_per_user`` (only covers rows where
reward_type='milestone') rejects any duplicate insert via an IntegrityError
caught inside a savepoint — so exactly one grant persists even if two sessions
race to evaluate at the same count.

Tests
-----
1. ``test_milestone_not_skipped`` — milestone at count=2; both activations run
   concurrently; asserts the milestone is granted exactly once after both
   Phase-2 evaluations also run concurrently.
2. ``test_milestone_not_doubled`` — milestone at count=1; both activations +
   evaluations run concurrently; unique constraint prevents double-grant.
3. ``test_per_activation_rewards_granted_for_each_user`` — control case
   ensuring base rewards survive the refactor.

Strategy
--------
File-based SQLite (aiosqlite) so two independent sessions truly share
committed state.  Each test runs Phase 1 (two concurrent activations) and
then Phase 2 (two concurrent milestone evaluations) via asyncio.gather.
Both sessions are asserted to complete without unexpected exceptions.
"""
from __future__ import annotations

import asyncio

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
    """File-based SQLite so two sessions share committed state."""
    url = f"sqlite+aiosqlite:///{tmp_path}/milestone_concurrent.db"
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

async def _seed_config_with_milestone(
    session: AsyncSession,
    milestone_count: int,
    milestone_days: int = 14,
) -> ReferralConfig:
    """Config with a single milestone that fires at *milestone_count* active referrals."""
    cfg = ReferralConfig(
        is_enabled=True,
        referrer_reward_days=7,
        referee_reward_days=3,
        milestones=[
            {
                "count": milestone_count,
                "type": "premium_days",
                "value": milestone_days,
                "label": f"+{milestone_days} дн. Premium (milestone {milestone_count})",
            }
        ],
        levels=[{"name": "Bronze", "min": 0, "emoji": "🥉", "color": "#CD7F32"}],
    )
    session.add(cfg)
    await session.flush()
    return cfg


async def _seed_pending_referral(
    session: AsyncSession,
    referrer_id: int,
    referred_id: int,
) -> Referral:
    ref = Referral(
        referrer_telegram_id=referrer_id,
        referred_telegram_id=referred_id,
        status="pending",
        referred_first_name=f"User{referred_id}",
    )
    session.add(ref)
    await session.flush()
    return ref


# ---------------------------------------------------------------------------
# Helpers: run each phase in isolated sessions
# ---------------------------------------------------------------------------

async def _activate_and_commit(
    session_factory,
    referred_id: int,
) -> tuple[Referral | None, list[dict], Exception | None]:
    """Phase 1: activate in its own session and commit.

    Returns (ref, rewards, None) on success or (None, [], exc) on any error.
    Errors are returned (not raised) so asyncio.gather collects both outcomes.
    """
    try:
        async with session_factory() as sess:
            repo = ReferralRepository(sess)
            ref, rewards = await repo.try_activate(
                referred_telegram_id=referred_id,
                has_business_connection=True,
            )
            await sess.commit()
            return ref, rewards, None
    except Exception as exc:  # noqa: BLE001
        return None, [], exc


async def _evaluate_milestones_and_commit(
    session_factory,
    referrer_id: int,
    referral_id: int | None,
) -> tuple[list[dict], Exception | None]:
    """Phase 2: evaluate milestones against committed state in its own session.

    Returns (milestone_rewards, None) on success or ([], exc) on error.
    """
    try:
        async with session_factory() as sess:
            repo = ReferralRepository(sess)
            ms_rewards = await repo.evaluate_and_grant_milestones(referrer_id, referral_id)
            await sess.commit()
            return ms_rewards, None
    except Exception as exc:  # noqa: BLE001
        return [], exc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_milestone_not_skipped_when_two_users_activate_concurrently(session_factory):
    """Milestone at count=2 fires exactly once after two concurrent activations.

    Both referred users (9001 and 9002) refer 9000.  The milestone fires when
    the referrer reaches 2 active referrals.

    OLD bug: both sessions saw count=1 inside the activation transaction
    (TOCTOU), so the count=2 milestone was silently skipped.

    FIX: Phase 2 evaluate_and_grant_milestones runs after both Phase 1 commits,
    so _count_active reads 2 and the milestone is granted exactly once.
    """
    referrer_id = 9000
    referred_a = 9001
    referred_b = 9002
    milestone_count = 2

    # ── Seed ─────────────────────────────────────────────────────────────────
    async with session_factory() as seed:
        await _seed_config_with_milestone(seed, milestone_count=milestone_count)
        await _seed_pending_referral(seed, referrer_id, referred_a)
        await _seed_pending_referral(seed, referrer_id, referred_b)
        await seed.commit()

    # ── Phase 1: concurrent activations ──────────────────────────────────────
    (ref_a, rw_a, ex_a), (ref_b, rw_b, ex_b) = await asyncio.gather(
        _activate_and_commit(session_factory, referred_a),
        _activate_and_commit(session_factory, referred_b),
    )

    # Both activations must complete without unexpected errors
    assert ex_a is None, (
        f"Session A (referred_a={referred_a}) raised an unexpected error: {ex_a!r}"
    )
    assert ex_b is None, (
        f"Session B (referred_b={referred_b}) raised an unexpected error: {ex_b!r}"
    )

    # Each activation must return base rewards
    assert rw_a, f"Session A returned no rewards — referred_a={referred_a} activation failed"
    assert rw_b, f"Session B returned no rewards — referred_b={referred_b} activation failed"
    assert ref_a is not None and ref_b is not None

    # ── Phase 2: concurrent milestone evaluations ─────────────────────────────
    # Both evaluators run after both activations committed, so _count_active=2.
    # Exactly one of them will insert the milestone log; the other will get an
    # IntegrityError from the partial unique index and skip gracefully.
    (ms_a, ms_ex_a), (ms_b, ms_ex_b) = await asyncio.gather(
        _evaluate_milestones_and_commit(session_factory, referrer_id, ref_a.id),
        _evaluate_milestones_and_commit(session_factory, referrer_id, ref_b.id),
    )

    assert ms_ex_a is None, (
        f"Phase-2 session A raised an unexpected error: {ms_ex_a!r}"
    )
    assert ms_ex_b is None, (
        f"Phase-2 session B raised an unexpected error: {ms_ex_b!r}"
    )

    # At least one Phase-2 call must have produced the milestone reward
    total_ms_grants = len(ms_a) + len(ms_b)
    assert total_ms_grants >= 1, (
        "Neither Phase-2 evaluation produced a milestone reward. "
        "The skip race was not fixed — both sessions read count < 2 "
        "or evaluate_and_grant_milestones is not being called."
    )

    # ── Verify DB: milestone log must exist exactly once ─────────────────────
    async with session_factory() as check:
        milestone_logs = (
            await check.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.user_telegram_id == referrer_id,
                    ReferralRewardLog.reward_type == "milestone",
                    ReferralRewardLog.reward_value == str(milestone_count),
                )
            )
        ).scalars().all()

        assert len(milestone_logs) == 1, (
            f"Expected exactly 1 milestone reward log for referrer {referrer_id} "
            f"at count={milestone_count}, but found {len(milestone_logs)}. "
            "A count of 0 means the milestone was skipped (TOCTOU bug — Phase 2 "
            "was not called or still reads stale state); "
            "a count >1 means the unique-constraint guard failed."
        )

        # Both referral rows must be active
        for rid in (referred_a, referred_b):
            rows = (
                await check.execute(
                    select(Referral).where(Referral.referred_telegram_id == rid)
                )
            ).scalars().all()
            assert len(rows) == 1, f"Expected 1 referral row for {rid}"
            assert rows[0].status == "active", (
                f"Referral for {rid} should be 'active', got {rows[0].status!r}"
            )


@pytest.mark.asyncio
async def test_milestone_not_doubled_when_first_activation_reaches_count(session_factory):
    """Milestone at count=1 fires exactly once even with concurrent Phase-2 evaluations.

    Both referred users refer the same referrer.  The milestone threshold is 1,
    so the FIRST activation already crosses it.  Both Phase-2 evaluators run
    concurrently; the partial unique index must reject the second grant via an
    IntegrityError caught inside a savepoint.  Neither session must propagate
    an unexpected exception.
    """
    referrer_id = 9100
    referred_a = 9101
    referred_b = 9102
    milestone_count = 1

    # ── Seed ─────────────────────────────────────────────────────────────────
    async with session_factory() as seed:
        await _seed_config_with_milestone(seed, milestone_count=milestone_count)
        await _seed_pending_referral(seed, referrer_id, referred_a)
        await _seed_pending_referral(seed, referrer_id, referred_b)
        await seed.commit()

    # ── Phase 1: concurrent activations ──────────────────────────────────────
    (ref_a, rw_a, ex_a), (ref_b, rw_b, ex_b) = await asyncio.gather(
        _activate_and_commit(session_factory, referred_a),
        _activate_and_commit(session_factory, referred_b),
    )

    assert ex_a is None, f"Phase-1 session A raised: {ex_a!r}"
    assert ex_b is None, f"Phase-1 session B raised: {ex_b!r}"
    assert rw_a, f"Session A returned no rewards for referred_a={referred_a}"
    assert rw_b, f"Session B returned no rewards for referred_b={referred_b}"
    assert ref_a is not None and ref_b is not None

    # ── Phase 2: concurrent milestone evaluations ─────────────────────────────
    (ms_a, ms_ex_a), (ms_b, ms_ex_b) = await asyncio.gather(
        _evaluate_milestones_and_commit(session_factory, referrer_id, ref_a.id),
        _evaluate_milestones_and_commit(session_factory, referrer_id, ref_b.id),
    )

    # Both Phase-2 sessions must complete without propagating an error.
    # A duplicate insert is caught internally (savepoint + IntegrityError) and
    # must NOT be visible to the caller.
    assert ms_ex_a is None, (
        f"Phase-2 session A raised an unexpected error: {ms_ex_a!r}"
    )
    assert ms_ex_b is None, (
        f"Phase-2 session B raised an unexpected error: {ms_ex_b!r}"
    )

    # ── Verify DB: milestone log must exist exactly once ─────────────────────
    async with session_factory() as check:
        milestone_logs = (
            await check.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.user_telegram_id == referrer_id,
                    ReferralRewardLog.reward_type == "milestone",
                    ReferralRewardLog.reward_value == str(milestone_count),
                )
            )
        ).scalars().all()

        assert len(milestone_logs) == 1, (
            f"Expected exactly 1 milestone reward log for referrer {referrer_id} "
            f"at count={milestone_count}, but found {len(milestone_logs)}. "
            "A count >1 means the duplicate-guard (unique constraint + savepoint) "
            "failed under concurrent load."
        )

        # Both referral rows must be active
        for rid in (referred_a, referred_b):
            rows = (
                await check.execute(
                    select(Referral).where(Referral.referred_telegram_id == rid)
                )
            ).scalars().all()
            assert len(rows) == 1, f"Expected 1 referral row for {rid}"
            assert rows[0].status == "active", (
                f"Referral for {rid} should be 'active', got {rows[0].status!r}"
            )


@pytest.mark.asyncio
async def test_per_activation_rewards_granted_for_each_user(session_factory):
    """Base rewards (per-activation + welcome) are unaffected by the milestone refactor.

    Control case: splitting milestone evaluation into Phase 2 must not suppress
    Phase-1 rewards for either user.
    """
    referrer_id = 9200
    referred_a = 9201
    referred_b = 9202

    async with session_factory() as seed:
        await _seed_config_with_milestone(seed, milestone_count=2)
        await _seed_pending_referral(seed, referrer_id, referred_a)
        await _seed_pending_referral(seed, referrer_id, referred_b)
        await seed.commit()

    # Phase 1
    (ref_a, rw_a, ex_a), (ref_b, rw_b, ex_b) = await asyncio.gather(
        _activate_and_commit(session_factory, referred_a),
        _activate_and_commit(session_factory, referred_b),
    )

    assert ex_a is None, f"Phase-1 session A raised: {ex_a!r}"
    assert ex_b is None, f"Phase-1 session B raised: {ex_b!r}"
    assert ref_a is not None and ref_b is not None

    # Phase 2
    (ms_a, ms_ex_a), (ms_b, ms_ex_b) = await asyncio.gather(
        _evaluate_milestones_and_commit(session_factory, referrer_id, ref_a.id),
        _evaluate_milestones_and_commit(session_factory, referrer_id, ref_b.id),
    )
    assert ms_ex_a is None, f"Phase-2 session A raised: {ms_ex_a!r}"
    assert ms_ex_b is None, f"Phase-2 session B raised: {ms_ex_b!r}"

    async with session_factory() as check:
        # Each referred user gets exactly one "welcome" log
        for rid in (referred_a, referred_b):
            welcome = (
                await check.execute(
                    select(ReferralRewardLog).where(
                        ReferralRewardLog.user_telegram_id == rid,
                        ReferralRewardLog.reward_type == "welcome",
                    )
                )
            ).scalars().all()
            assert len(welcome) == 1, (
                f"Expected 1 welcome log for referred user {rid}, got {len(welcome)}"
            )

        # Referrer gets exactly two "per_activation" logs (one per referred user)
        per_act = (
            await check.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.user_telegram_id == referrer_id,
                    ReferralRewardLog.reward_type == "per_activation",
                )
            )
        ).scalars().all()
        assert len(per_act) == 2, (
            f"Expected 2 per_activation logs for referrer {referrer_id}, got {len(per_act)}"
        )

        # Milestone at count=2 must be granted exactly once (Phase 2 handles it)
        milestone_logs = (
            await check.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.user_telegram_id == referrer_id,
                    ReferralRewardLog.reward_type == "milestone",
                )
            )
        ).scalars().all()
        assert len(milestone_logs) == 1, (
            f"Expected exactly 1 milestone log for referrer {referrer_id}, "
            f"got {len(milestone_logs)}"
        )
