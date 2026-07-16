"""Test: milestones are granted when referrals are activated via the admin
import path (``admin_set_status`` → ``evaluate_and_grant_milestones``).

Background
----------
The mini-app activation path (``try_activate``) always calls
``evaluate_and_grant_milestones`` in Phase 2 after committing.  The admin
panel's ``admin_set_status`` endpoint was previously missing this Phase 2
call, so any milestone crossing triggered by an admin-activated referral was
silently skipped.

The fix adds an explicit Phase 2 call in ``admin_referral_adjust`` immediately
after the Phase 1 commit whenever the new status is ``"active"``.

These tests verify that behavior at the repository level:

1. ``test_milestone_granted_after_admin_activation`` — admin activates a
   single referral that crosses the milestone threshold; milestone is granted.
2. ``test_milestone_granted_after_bulk_admin_import`` — admin activates
   multiple referrals one by one (simulating a bulk import); all milestones
   crossed during the import are granted exactly once each.
3. ``test_non_active_status_change_does_not_grant_milestone`` — setting status
   to ``"fraud"`` or ``"pending"`` must NOT trigger a milestone grant.
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
    """File-based SQLite so sessions share committed state."""
    url = f"sqlite+aiosqlite:///{tmp_path}/admin_import_milestone.db"
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

async def _seed_config(
    session: AsyncSession,
    milestones: list[dict],
    referrer_reward_days: int = 7,
    referee_reward_days: int = 3,
) -> ReferralConfig:
    cfg = ReferralConfig(
        is_enabled=True,
        referrer_reward_days=referrer_reward_days,
        referee_reward_days=referee_reward_days,
        milestones=milestones,
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
# Helper: simulate what admin_referral_adjust does at the repository level
# ---------------------------------------------------------------------------

async def _admin_activate_with_milestones(
    session_factory,
    referral_id: int,
) -> list[dict]:
    """Replicate the two-phase logic in admin_referral_adjust.

    Phase 1: admin_set_status → commit
    Phase 2: evaluate_and_grant_milestones → commit (if any granted)

    Returns the list of milestone reward dicts granted in Phase 2.
    """
    # Phase 1 — activate and commit
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ok, ref = await repo.admin_set_status(referral_id, "active")
        assert ok, f"admin_set_status returned False for referral_id={referral_id}"
        assert ref is not None
        referrer_id = ref.referrer_telegram_id
        ref_id = ref.id
        await sess.commit()

    # Phase 2 — evaluate milestones against committed state
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ms_rewards = await repo.evaluate_and_grant_milestones(referrer_id, ref_id)
        if ms_rewards:
            await sess.commit()
        return ms_rewards


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_milestone_granted_after_admin_activation(session_factory):
    """Admin activates one referral that crosses the count=1 milestone threshold.

    The milestone must be present in the reward log exactly once after the
    two-phase admin import flow completes.
    """
    referrer_id = 8000
    referred_id = 8001
    milestone_count = 1
    milestone_days = 14

    # Seed
    async with session_factory() as seed:
        await _seed_config(seed, milestones=[{
            "count": milestone_count,
            "type": "premium_days",
            "value": milestone_days,
            "label": f"+{milestone_days} дн. Premium (milestone {milestone_count})",
        }])
        ref = await _seed_pending_referral(seed, referrer_id, referred_id)
        referral_id = ref.id
        await seed.commit()

    # Admin import: activate + evaluate milestones
    ms_rewards = await _admin_activate_with_milestones(session_factory, referral_id)

    assert len(ms_rewards) == 1, (
        f"Expected 1 milestone reward from admin import, got {len(ms_rewards)}. "
        "evaluate_and_grant_milestones was not called or returned empty — "
        "the admin path is silently skipping milestones."
    )
    assert ms_rewards[0]["milestone"]["count"] == milestone_count

    # Verify DB state
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
            f"Expected exactly 1 milestone log for referrer={referrer_id} "
            f"at count={milestone_count}, found {len(milestone_logs)}."
        )

        # Referral must be active
        referral_row = (
            await check.execute(
                select(Referral).where(Referral.referred_telegram_id == referred_id)
            )
        ).scalar_one()
        assert referral_row.status == "active"
        assert referral_row.activated_at is not None


@pytest.mark.asyncio
async def test_milestone_granted_after_bulk_admin_import(session_factory):
    """Admin activates 5 referrals sequentially (bulk import simulation).

    Milestones at count=3 and count=5 must each be granted exactly once.
    """
    referrer_id = 8100
    referred_ids = [8101, 8102, 8103, 8104, 8105]
    milestones = [
        {"count": 3, "type": "premium_days", "value": 7,
         "label": "+7 дн. Premium (milestone 3)"},
        {"count": 5, "type": "premium_days", "value": 14,
         "label": "+14 дн. Premium (milestone 5)"},
    ]

    # Seed all as pending
    referral_ids: list[int] = []
    async with session_factory() as seed:
        await _seed_config(seed, milestones=milestones)
        for rid in referred_ids:
            ref = await _seed_pending_referral(seed, referrer_id, rid)
            referral_ids.append(ref.id)
        await seed.commit()

    # Admin bulk import: activate each one by one (sequential, like a loop)
    all_ms_rewards: list[dict] = []
    for ref_id in referral_ids:
        ms = await _admin_activate_with_milestones(session_factory, ref_id)
        all_ms_rewards.extend(ms)

    # Both milestone thresholds (3 and 5) must have been crossed
    granted_counts = {r["milestone"]["count"] for r in all_ms_rewards}
    assert 3 in granted_counts, (
        "Milestone at count=3 was not granted during bulk admin import. "
        f"Granted milestone counts: {granted_counts}"
    )
    assert 5 in granted_counts, (
        "Milestone at count=5 was not granted during bulk admin import. "
        f"Granted milestone counts: {granted_counts}"
    )

    # Each milestone must appear exactly once in the DB
    async with session_factory() as check:
        for ms_count in (3, 5):
            logs = (
                await check.execute(
                    select(ReferralRewardLog).where(
                        ReferralRewardLog.user_telegram_id == referrer_id,
                        ReferralRewardLog.reward_type == "milestone",
                        ReferralRewardLog.reward_value == str(ms_count),
                    )
                )
            ).scalars().all()

            assert len(logs) == 1, (
                f"Expected exactly 1 milestone log for referrer={referrer_id} "
                f"at count={ms_count}, found {len(logs)}. "
                "Either the milestone was skipped (not granted) or was double-granted."
            )

        # All 5 referrals must be active
        for rid in referred_ids:
            row = (
                await check.execute(
                    select(Referral).where(Referral.referred_telegram_id == rid)
                )
            ).scalar_one()
            assert row.status == "active", (
                f"Referral for referred_id={rid} should be 'active', got {row.status!r}"
            )


@pytest.mark.asyncio
async def test_non_active_status_change_does_not_grant_milestone(session_factory):
    """Setting status to 'fraud' or 'pending' must NOT trigger milestone evaluation.

    Only the 'active' branch calls evaluate_and_grant_milestones.
    """
    referrer_id = 8200
    referred_ids = [8201, 8202]
    milestone_count = 1

    async with session_factory() as seed:
        await _seed_config(seed, milestones=[{
            "count": milestone_count,
            "type": "premium_days",
            "value": 7,
            "label": "+7 дн. Premium (milestone 1)",
        }])
        refs = []
        for rid in referred_ids:
            ref = await _seed_pending_referral(seed, referrer_id, rid)
            refs.append(ref.id)
        await seed.commit()

    # Mark first referral as fraud (no milestone should fire)
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ok, ref = await repo.admin_set_status(refs[0], "fraud", "test fraud")
        assert ok
        await sess.commit()
        # No Phase 2 — fraud path does not call evaluate_and_grant_milestones

    # Mark second referral back to pending (no milestone should fire)
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ok, ref = await repo.admin_set_status(refs[1], "pending")
        assert ok
        await sess.commit()
        # No Phase 2 — pending path does not call evaluate_and_grant_milestones

    # Verify no milestone logs exist
    async with session_factory() as check:
        logs = (
            await check.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.user_telegram_id == referrer_id,
                    ReferralRewardLog.reward_type == "milestone",
                )
            )
        ).scalars().all()

        assert len(logs) == 0, (
            f"Expected 0 milestone logs when only fraud/pending statuses were set, "
            f"found {len(logs)}. Milestone evaluation must only fire for 'active'."
        )


# ---------------------------------------------------------------------------
# Helper: full two-phase admin activation (status + activation rewards)
# ---------------------------------------------------------------------------

async def _admin_activate_full(
    session_factory,
    referral_id: int,
) -> tuple[list[dict], list[dict]]:
    """Replicate the full three-phase logic in admin_referral_adjust.

    Phase 1 : admin_set_status → commit
    Phase 1b: admin_grant_per_activation_rewards → commit (if any granted)
    Phase 2 : evaluate_and_grant_milestones → commit (if any granted)

    Returns (activation_rewards, milestone_rewards).
    """
    # Phase 1 — activate and commit
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ok, ref = await repo.admin_set_status(referral_id, "active")
        assert ok, f"admin_set_status returned False for referral_id={referral_id}"
        referrer_id = ref.referrer_telegram_id
        ref_id = ref.id
        await sess.commit()

    # Phase 1b — per-activation + welcome rewards
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        # Re-fetch ref so session owns the object
        ref_row = (
            await sess.execute(select(Referral).where(Referral.id == ref_id))
        ).scalar_one()
        activation_rewards = await repo.admin_grant_per_activation_rewards(ref_row)
        if activation_rewards:
            await sess.commit()

    # Phase 2 — milestones
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ms_rewards = await repo.evaluate_and_grant_milestones(referrer_id, ref_id)
        if ms_rewards:
            await sess.commit()

    return activation_rewards, ms_rewards


# ---------------------------------------------------------------------------
# Per-activation + welcome reward tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_per_activation_and_welcome_rewards_granted_on_admin_activate(
    session_factory,
):
    """Admin activating a pending referral must grant the per-activation reward
    to the referrer and the welcome reward to the referee.
    """
    referrer_id = 9000
    referred_id = 9001
    referrer_days = 7
    referee_days = 3

    async with session_factory() as seed:
        await _seed_config(
            seed,
            milestones=[],
            referrer_reward_days=referrer_days,
            referee_reward_days=referee_days,
        )
        ref = await _seed_pending_referral(seed, referrer_id, referred_id)
        referral_id = ref.id
        await seed.commit()

    activation_rewards, ms_rewards = await _admin_activate_full(
        session_factory, referral_id
    )

    assert ms_rewards == [], "No milestones configured — none should fire"

    reward_types = {r["type"] for r in activation_rewards}
    assert "per_activation" in reward_types, (
        "per_activation reward was not granted to the referrer during admin activation"
    )
    assert "welcome" in reward_types, (
        "welcome reward was not granted to the referee during admin activation"
    )

    # Verify DB: one per_activation log for referrer, one welcome log for referee
    async with session_factory() as check:
        per_act_logs = (
            await check.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.referral_id == referral_id,
                    ReferralRewardLog.user_telegram_id == referrer_id,
                    ReferralRewardLog.reward_type == "per_activation",
                )
            )
        ).scalars().all()
        assert len(per_act_logs) == 1, (
            f"Expected 1 per_activation log for referrer={referrer_id}, "
            f"found {len(per_act_logs)}"
        )

        welcome_logs = (
            await check.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.referral_id == referral_id,
                    ReferralRewardLog.user_telegram_id == referred_id,
                    ReferralRewardLog.reward_type == "welcome",
                )
            )
        ).scalars().all()
        assert len(welcome_logs) == 1, (
            f"Expected 1 welcome log for referred={referred_id}, "
            f"found {len(welcome_logs)}"
        )

        # Premium subscriptions must exist for both parties
        for uid, label in [(referrer_id, "referrer"), (referred_id, "referee")]:
            sub = (
                await check.execute(
                    select(UserSubscription).where(
                        UserSubscription.user_telegram_id == uid,
                        UserSubscription.is_active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            assert sub is not None, (
                f"Expected an active subscription for {label} (uid={uid}) "
                "after admin activation — none found"
            )


@pytest.mark.asyncio
async def test_admin_activation_rewards_are_idempotent(session_factory):
    """Calling admin_grant_per_activation_rewards twice for the same referral
    must not double-grant either reward.
    """
    referrer_id = 9100
    referred_id = 9101
    referrer_days = 7
    referee_days = 3

    async with session_factory() as seed:
        await _seed_config(
            seed,
            milestones=[],
            referrer_reward_days=referrer_days,
            referee_reward_days=referee_days,
        )
        ref = await _seed_pending_referral(seed, referrer_id, referred_id)
        referral_id = ref.id
        await seed.commit()

    # First activation — rewards should fire
    activation_rewards_1, _ = await _admin_activate_full(session_factory, referral_id)
    assert len(activation_rewards_1) == 2, (
        f"Expected 2 activation rewards on first call, got {len(activation_rewards_1)}"
    )

    # Second call — rewards already logged, nothing new should be granted
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ref_row = (
            await sess.execute(select(Referral).where(Referral.id == referral_id))
        ).scalar_one()
        activation_rewards_2 = await repo.admin_grant_per_activation_rewards(ref_row)
        await sess.commit()

    assert activation_rewards_2 == [], (
        f"Expected 0 new activation rewards on second call (idempotency), "
        f"got {len(activation_rewards_2)}: {activation_rewards_2}"
    )

    # DB: still exactly one log per reward type
    async with session_factory() as check:
        for uid, rtype in [
            (referrer_id, "per_activation"),
            (referred_id, "welcome"),
        ]:
            logs = (
                await check.execute(
                    select(ReferralRewardLog).where(
                        ReferralRewardLog.referral_id == referral_id,
                        ReferralRewardLog.user_telegram_id == uid,
                        ReferralRewardLog.reward_type == rtype,
                    )
                )
            ).scalars().all()
            assert len(logs) == 1, (
                f"Expected exactly 1 {rtype} log for uid={uid} after two calls, "
                f"found {len(logs)} — double-grant detected"
            )


@pytest.mark.asyncio
async def test_subscription_status_reflects_admin_activation_rewards(session_factory):
    """Integration test: after admin activation the subscription-status query
    used by the /app/api/subscription/status endpoint returns is_active=True
    with the correct expiry for both referrer and referee.

    This test exercises the full chain:
      admin_set_status → admin_grant_per_activation_rewards → _grant_premium
      → UserSubscription row → SubscriptionRepository.get_active_subscription

    If _grant_premium silently fails or writes to the wrong table,
    get_active_subscription returns None and the assertions below fail.
    """
    import datetime as dt
    from app.repositories.subscription_repository import SubscriptionRepository

    referrer_id = 9300
    referred_id = 9301
    referrer_days = 7
    referee_days = 3

    async with session_factory() as seed:
        await _seed_config(
            seed,
            milestones=[],
            referrer_reward_days=referrer_days,
            referee_reward_days=referee_days,
        )
        ref = await _seed_pending_referral(seed, referrer_id, referred_id)
        referral_id = ref.id
        await seed.commit()

    # Run the full admin activation (mirrors what admin_referral_adjust does)
    activation_rewards, _ = await _admin_activate_full(session_factory, referral_id)

    assert len(activation_rewards) == 2, (
        f"Expected 2 activation rewards (per_activation + welcome), "
        f"got {len(activation_rewards)}: {activation_rewards}"
    )

    # Now query subscription status the same way the endpoint does —
    # via SubscriptionRepository.get_active_subscription.
    async with session_factory() as check:
        sub_repo = SubscriptionRepository(check)
        now = dt.datetime.now(dt.timezone.utc)

        # ── Referrer must have an active Premium subscription ─────────────────
        referrer_sub = await sub_repo.get_active_subscription(referrer_id)
        assert referrer_sub is not None, (
            f"get_active_subscription returned None for referrer (uid={referrer_id}) "
            "after admin activation — _grant_premium did not create a subscription row "
            "or wrote it to the wrong place."
        )
        assert referrer_sub.is_active is True, (
            f"Referrer subscription is_active={referrer_sub.is_active!r}, expected True"
        )
        # SQLite may return timezone-naive datetimes; normalise before comparing.
        referrer_expires = referrer_sub.expires_at
        if referrer_expires.tzinfo is None:
            referrer_expires = referrer_expires.replace(tzinfo=dt.timezone.utc)
        referrer_days_left = (referrer_expires - now).days
        assert referrer_days_left >= referrer_days - 1, (
            f"Referrer expiry is too soon: days_left={referrer_days_left}, "
            f"expected ~{referrer_days} days. expires_at={referrer_sub.expires_at}"
        )

        # ── Referee must have an active Premium subscription ──────────────────
        referee_sub = await sub_repo.get_active_subscription(referred_id)
        assert referee_sub is not None, (
            f"get_active_subscription returned None for referee (uid={referred_id}) "
            "after admin activation — _grant_premium did not create a subscription row "
            "or wrote it to the wrong place."
        )
        assert referee_sub.is_active is True, (
            f"Referee subscription is_active={referee_sub.is_active!r}, expected True"
        )
        referee_expires = referee_sub.expires_at
        if referee_expires.tzinfo is None:
            referee_expires = referee_expires.replace(tzinfo=dt.timezone.utc)
        referee_days_left = (referee_expires - now).days
        assert referee_days_left >= referee_days - 1, (
            f"Referee expiry is too soon: days_left={referee_days_left}, "
            f"expected ~{referee_days} days. expires_at={referee_sub.expires_at}"
        )


@pytest.mark.asyncio
async def test_zero_reward_days_skips_grants(session_factory):
    """When referrer_reward_days=0 and referee_reward_days=0, no per-activation
    or welcome rewards should be granted even via the admin path.
    """
    referrer_id = 9200
    referred_id = 9201

    async with session_factory() as seed:
        await _seed_config(
            seed,
            milestones=[],
            referrer_reward_days=0,
            referee_reward_days=0,
        )
        ref = await _seed_pending_referral(seed, referrer_id, referred_id)
        referral_id = ref.id
        await seed.commit()

    activation_rewards, _ = await _admin_activate_full(session_factory, referral_id)

    assert activation_rewards == [], (
        f"Expected no activation rewards when reward_days=0, got {activation_rewards}"
    )

    async with session_factory() as check:
        logs = (
            await check.execute(
                select(ReferralRewardLog).where(
                    ReferralRewardLog.referral_id == referral_id,
                    ReferralRewardLog.reward_type.in_(["welcome", "per_activation"]),
                )
            )
        ).scalars().all()
        assert len(logs) == 0, (
            f"Expected 0 activation reward logs when days=0, found {len(logs)}"
        )
