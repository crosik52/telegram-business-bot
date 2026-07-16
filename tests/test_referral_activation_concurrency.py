"""Concurrency test: two simultaneous try_activate calls for the same referred
user produce exactly one activation — no double reward grants.

Before the fix, both calls could read the referral as "pending" before either
committed, then both mark it active and grant rewards — doubling the payout.

After the fix, try_activate selects the Referral row WITH FOR UPDATE.
Under PostgreSQL this serialises the two transactions via a row-level lock.
Under SQLite (used here for speed) writes are serialised at the database
level: one transaction commits first; the other either sees no pending row
(because status is already "active") or is rejected with a database-locked
OperationalError — either way, rewards are granted exactly once.

Assertions:
- Exactly one of the two concurrent calls returns a non-empty reward list.
- Only one ReferralRewardLog row per reward type exists for each user.
- Only one UserSubscription row exists for the referee and the referrer.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.referral import Referral, ReferralConfig, ReferralRewardLog
from app.models.subscription import UserSubscription
from app.repositories.referral_repository import ReferralRepository


# ---------------------------------------------------------------------------
# Fixtures — file-based SQLite so two independent sessions share state
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def db_path(tmp_path):
    """Return the path to a temporary SQLite file."""
    return str(tmp_path / "referral_test.db")


@pytest_asyncio.fixture()
async def engine(db_path):
    url = f"sqlite+aiosqlite:///{db_path}"
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

async def _seed_config(session: AsyncSession) -> ReferralConfig:
    cfg = ReferralConfig(
        is_enabled=True,
        referrer_reward_days=7,
        referee_reward_days=3,
        milestones=[],   # no milestones to keep test simple
        levels=[{"name": "Bronze", "min": 0, "max": None, "emoji": "🥉", "color": "#CD7F32"}],
    )
    session.add(cfg)
    await session.flush()
    return cfg


async def _seed_referral(
    session: AsyncSession,
    referrer_id: int,
    referred_id: int,
) -> Referral:
    ref = Referral(
        referrer_telegram_id=referrer_id,
        referred_telegram_id=referred_id,
        status="pending",
    )
    session.add(ref)
    await session.flush()
    return ref


# ---------------------------------------------------------------------------
# Helper: run try_activate in its own session, return (rewards, error)
# ---------------------------------------------------------------------------

async def _activate(session_factory, referred_id: int) -> tuple[list[dict], Exception | None]:
    """Call try_activate in an isolated session.

    Returns (rewards_list, None) on success, or ([], exception) if the
    session fails with an OperationalError (SQLite database-locked) or any
    other error.
    """
    try:
        async with session_factory() as sess:
            repo = ReferralRepository(sess)
            _ref, rewards = await repo.try_activate(
                referred_telegram_id=referred_id,
                has_business_connection=True,
            )
            await sess.commit()
            return rewards, None
    except Exception as exc:  # noqa: BLE001
        return [], exc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_only_one_activation_succeeds(session_factory):
    """Exactly one of two concurrent try_activate calls returns rewards."""
    referrer_id, referred_id = 8001, 8002

    async with session_factory() as seed:
        await _seed_config(seed)
        await _seed_referral(seed, referrer_id, referred_id)
        await seed.commit()

    results = await asyncio.gather(
        _activate(session_factory, referred_id),
        _activate(session_factory, referred_id),
    )
    (rw_a, ex_a), (rw_b, ex_b) = results

    successful_reward_lists = [rw for rw in (rw_a, rw_b) if rw]
    assert len(successful_reward_lists) == 1, (
        f"Expected exactly 1 activation to succeed with rewards; got reward lists: "
        f"{rw_a!r} and {rw_b!r}"
    )


@pytest.mark.asyncio
async def test_reward_logs_created_exactly_once(session_factory):
    """Only one ReferralRewardLog per reward type per user after concurrent activation."""
    referrer_id, referred_id = 8101, 8102

    async with session_factory() as seed:
        await _seed_config(seed)
        await _seed_referral(seed, referrer_id, referred_id)
        await seed.commit()

    await asyncio.gather(
        _activate(session_factory, referred_id),
        _activate(session_factory, referred_id),
    )

    async with session_factory() as check:
        # "welcome" log for referee — must be exactly 1
        welcome_logs = (
            await check.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.user_telegram_id == referred_id,
                    ReferralRewardLog.reward_type == "welcome",
                )
            )
        ).scalars().all()
        assert len(welcome_logs) == 1, (
            f"Expected 1 welcome reward log for referee, got {len(welcome_logs)}"
        )

        # "per_activation" log for referrer — must be exactly 1
        per_act_logs = (
            await check.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.user_telegram_id == referrer_id,
                    ReferralRewardLog.reward_type == "per_activation",
                )
            )
        ).scalars().all()
        assert len(per_act_logs) == 1, (
            f"Expected 1 per_activation reward log for referrer, got {len(per_act_logs)}"
        )


@pytest.mark.asyncio
async def test_subscription_created_exactly_once_per_user(session_factory):
    """Only one UserSubscription per user exists after concurrent activation."""
    referrer_id, referred_id = 8201, 8202

    async with session_factory() as seed:
        await _seed_config(seed)
        await _seed_referral(seed, referrer_id, referred_id)
        await seed.commit()

    await asyncio.gather(
        _activate(session_factory, referred_id),
        _activate(session_factory, referred_id),
    )

    async with session_factory() as check:
        for uid, label in [(referred_id, "referee"), (referrer_id, "referrer")]:
            subs = (
                await check.execute(
                    select(UserSubscription).where(
                        UserSubscription.user_telegram_id == uid,
                    )
                )
            ).scalars().all()
            assert len(subs) == 1, (
                f"Expected exactly 1 subscription for {label} (uid={uid}), got {len(subs)}"
            )


@pytest.mark.asyncio
async def test_referral_status_is_active_exactly_once(session_factory):
    """After concurrent activation the referral row has status='active' once."""
    referrer_id, referred_id = 8301, 8302

    async with session_factory() as seed:
        await _seed_config(seed)
        await _seed_referral(seed, referrer_id, referred_id)
        await seed.commit()

    await asyncio.gather(
        _activate(session_factory, referred_id),
        _activate(session_factory, referred_id),
    )

    async with session_factory() as check:
        refs = (
            await check.execute(
                select(Referral).where(
                    Referral.referred_telegram_id == referred_id,
                )
            )
        ).scalars().all()
        assert len(refs) == 1, f"Expected 1 referral row, got {len(refs)}"
        assert refs[0].status == "active", (
            f"Referral should be 'active', got {refs[0].status!r}"
        )
        assert refs[0].activated_at is not None, "activated_at must be set"
