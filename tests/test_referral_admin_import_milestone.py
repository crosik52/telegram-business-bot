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


# ---------------------------------------------------------------------------
# Extension-branch test: _grant_premium adds days to an existing subscription
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grant_premium_extends_existing_subscription(session_factory):
    """_grant_premium must add N days to the existing expires_at, not replace it.

    Scenario
    --------
    1. Seed a user (referrer) with an already-active subscription expiring 10 days
       from now.
    2. Admin-activate a referral whose referrer is that user; the config grants
       ``referrer_reward_days`` (7) days via ``admin_grant_per_activation_rewards``
       → ``_grant_premium``.
    3. Assert that the subscription's ``expires_at`` moved forward by exactly 7
       days from the *original* expiry, not from ``now``.

    If the extension branch is broken and ``_grant_premium`` creates a fresh
    subscription starting from now instead of extending the existing one, the
    final ``expires_at`` would be ≈ ``now + 7`` (≈ 17 days from now) rather
    than ``original_expires_at + 7`` (= 17 days from now **if** measured from the
    original expiry).  The test pins the original expiry precisely so that the
    two outcomes are distinguishable.
    """
    import datetime as dt

    referrer_id = 9500
    referred_id = 9501
    referrer_reward_days = 7
    existing_days_remaining = 10

    async with session_factory() as seed:
        await _seed_config(
            seed,
            milestones=[],
            referrer_reward_days=referrer_reward_days,
            referee_reward_days=0,  # irrelevant for this test
        )
        ref = await _seed_pending_referral(seed, referrer_id, referred_id)
        referral_id = ref.id

        # Seed an existing active subscription for the referrer
        now = dt.datetime.now(dt.timezone.utc)
        original_expires_at = now + dt.timedelta(days=existing_days_remaining)
        existing_sub = UserSubscription(
            user_telegram_id=referrer_id,
            is_active=True,
            started_at=now - dt.timedelta(days=1),
            expires_at=original_expires_at,
            granted_by_admin=True,
            stars_paid=0,
        )
        seed.add(existing_sub)
        await seed.commit()

    # Phase 1: activate the referral
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ok, ref = await repo.admin_set_status(referral_id, "active")
        assert ok
        ref_id = ref.id
        await sess.commit()

    # Phase 1b: grant per-activation rewards (calls _grant_premium for referrer)
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ref_row = (
            await sess.execute(select(Referral).where(Referral.id == ref_id))
        ).scalar_one()
        activation_rewards = await repo.admin_grant_per_activation_rewards(ref_row)
        await sess.commit()

    # Exactly one reward (per_activation for referrer; referee_reward_days=0)
    assert len(activation_rewards) == 1, (
        f"Expected 1 activation reward (per_activation), got {len(activation_rewards)}"
    )
    assert activation_rewards[0]["type"] == "per_activation"

    # Verify the subscription was EXTENDED, not replaced
    async with session_factory() as check:
        sub = (
            await check.execute(
                select(UserSubscription).where(
                    UserSubscription.user_telegram_id == referrer_id,
                    UserSubscription.is_active.is_(True),
                ).order_by(UserSubscription.expires_at.desc()).limit(1)
            )
        ).scalar_one_or_none()

        assert sub is not None, (
            f"No active subscription found for referrer (uid={referrer_id}) "
            "after _grant_premium — subscription was not created or is_active=False."
        )

        # Normalise timezone for SQLite (may return naive datetimes)
        actual_expires = sub.expires_at
        if actual_expires.tzinfo is None:
            actual_expires = actual_expires.replace(tzinfo=dt.timezone.utc)

        expected_expires = original_expires_at + dt.timedelta(days=referrer_reward_days)

        delta_seconds = abs((actual_expires - expected_expires).total_seconds())
        assert delta_seconds < 5, (
            f"expires_at was not extended by exactly {referrer_reward_days} days "
            f"from the original expiry.\n"
            f"  original_expires_at : {original_expires_at.isoformat()}\n"
            f"  expected_expires_at : {expected_expires.isoformat()}\n"
            f"  actual_expires_at   : {actual_expires.isoformat()}\n"
            f"  delta               : {delta_seconds:.1f}s\n"
            "This suggests _grant_premium created a new subscription from 'now' "
            "instead of extending the existing one."
        )


# ---------------------------------------------------------------------------
# Create-branch test: _grant_premium creates a fresh subscription when the
# previous one has already expired (or never existed).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grant_premium_creates_new_subscription_when_previous_expired(
    session_factory,
):
    """_grant_premium must create a new active subscription when no active one exists.

    Scenario A — expired subscription present
    -----------------------------------------
    1. Seed the user (referrer) with a subscription that already expired 5 days ago
       (is_active=True but expires_at < now).
    2. Admin-activate a referral whose referrer is that user; the config grants
       ``referrer_reward_days`` (14) days.
    3. Assert that a *new* ``UserSubscription`` row is created with
       ``is_active=True``, ``started_at ≈ now``, and ``expires_at ≈ now + 14 days``.

    Scenario B — no subscription at all
    ------------------------------------
    4. Repeat the same grant for a second user who has never had a subscription.
    5. Assert the same properties on the newly created row.

    If the create-branch of ``_grant_premium`` is broken — e.g. it silently
    skips the INSERT, sets ``is_active=False``, or uses the wrong ``started_at``
    — the assertions below will fail and expose the regression.
    """
    import datetime as dt

    referrer_id_expired = 9600   # has a lapsed subscription
    referrer_id_fresh   = 9601   # has never had a subscription
    referred_id_a       = 9602
    referred_id_b       = 9603
    grant_days          = 14

    # ── Seed ─────────────────────────────────────────────────────────────────
    async with session_factory() as seed:
        await _seed_config(
            seed,
            milestones=[],
            referrer_reward_days=grant_days,
            referee_reward_days=0,  # keep the referee out of the picture
        )

        # Referral A — referrer has an already-expired subscription
        ref_a = await _seed_pending_referral(seed, referrer_id_expired, referred_id_a)
        referral_id_a = ref_a.id

        now_seed = dt.datetime.now(dt.timezone.utc)
        expired_sub = UserSubscription(
            user_telegram_id=referrer_id_expired,
            is_active=True,                          # flag still True, but time has passed
            started_at=now_seed - dt.timedelta(days=35),
            expires_at=now_seed - dt.timedelta(days=5),  # lapsed 5 days ago
            granted_by_admin=True,
            stars_paid=0,
        )
        seed.add(expired_sub)

        # Referral B — referrer has no subscription at all
        ref_b = await _seed_pending_referral(seed, referrer_id_fresh, referred_id_b)
        referral_id_b = ref_b.id

        await seed.commit()

    # ── Activate referral A (expired-sub user) ───────────────────────────────
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ok, ref = await repo.admin_set_status(referral_id_a, "active")
        assert ok
        ref_id_a = ref.id
        await sess.commit()

    before_grant_a = dt.datetime.now(dt.timezone.utc)
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ref_row = (
            await sess.execute(select(Referral).where(Referral.id == ref_id_a))
        ).scalar_one()
        await repo.admin_grant_per_activation_rewards(ref_row)
        await sess.commit()
    after_grant_a = dt.datetime.now(dt.timezone.utc)

    # ── Activate referral B (no-sub user) ────────────────────────────────────
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ok, ref = await repo.admin_set_status(referral_id_b, "active")
        assert ok
        ref_id_b = ref.id
        await sess.commit()

    before_grant_b = dt.datetime.now(dt.timezone.utc)
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        ref_row = (
            await sess.execute(select(Referral).where(Referral.id == ref_id_b))
        ).scalar_one()
        await repo.admin_grant_per_activation_rewards(ref_row)
        await sess.commit()
    after_grant_b = dt.datetime.now(dt.timezone.utc)

    # ── Verify both users received a fresh active subscription ───────────────
    async with session_factory() as check:
        for uid, before, after, label in [
            (referrer_id_expired, before_grant_a, after_grant_a, "expired-sub user"),
            (referrer_id_fresh,   before_grant_b, after_grant_b, "no-sub user"),
        ]:
            # Fetch the subscription with the latest expires_at (the newly created one)
            sub = (
                await check.execute(
                    select(UserSubscription)
                    .where(
                        UserSubscription.user_telegram_id == uid,
                        UserSubscription.is_active.is_(True),
                        UserSubscription.expires_at > after,   # must be in the future
                    )
                    .order_by(UserSubscription.expires_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            assert sub is not None, (
                f"No active future subscription found for {label} (uid={uid}) "
                "after _grant_premium — the create-branch did not insert a new row "
                "or set is_active=False."
            )

            # Normalise timezone (SQLite may return naive datetimes)
            def _tz(ts: dt.datetime) -> dt.datetime:
                return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)

            started  = _tz(sub.started_at)
            expires  = _tz(sub.expires_at)

            # started_at must be within the grant window (≈ now at grant time)
            assert before <= started <= after + dt.timedelta(seconds=2), (
                f"{label}: started_at={started.isoformat()} is outside the expected "
                f"window [{before.isoformat()}, {after.isoformat()}]. "
                "_grant_premium must set started_at=now when creating a new subscription."
            )

            # expires_at must be started_at + grant_days (within a small tolerance)
            expected_expires = started + dt.timedelta(days=grant_days)
            delta_s = abs((expires - expected_expires).total_seconds())
            assert delta_s < 5, (
                f"{label}: expires_at={expires.isoformat()} differs from "
                f"started_at + {grant_days} days = {expected_expires.isoformat()} "
                f"by {delta_s:.1f}s. "
                "_grant_premium must set expires_at = now + days for a new subscription."
            )


# ---------------------------------------------------------------------------
# Tie-breaking test: _grant_premium picks the row with the latest expires_at
# when a user has multiple active subscriptions.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grant_premium_extends_latest_subscription_when_multiple_active(
    session_factory,
):
    """_grant_premium must extend the row with the furthest-future expires_at
    when a user has two overlapping active subscriptions.

    Scenario
    --------
    1. Seed a user with two active subscriptions:
       - ``sub_earlier`` expires 10 days from now.
       - ``sub_later``  expires 20 days from now  (the later one — should be picked).
    2. Call ``_grant_premium`` with 7 extra days.
    3. Assert that ``sub_later.expires_at`` moved forward by exactly 7 days.
    4. Assert that ``sub_earlier.expires_at`` is unchanged.

    This exercises the ``.order_by(UserSubscription.expires_at.desc()).limit(1)``
    tie-breaking in ``_grant_premium``.  If the ORDER BY were missing or reversed,
    ``sub_earlier`` would be extended while ``sub_later`` (the row the endpoint
    reads) would stay put — and the user would see no change in their expiry.
    """
    import datetime as dt

    user_id = 9700
    grant_days = 7
    earlier_days = 10
    later_days = 20

    async with session_factory() as seed:
        await _seed_config(seed, milestones=[], referrer_reward_days=0, referee_reward_days=0)

        now = dt.datetime.now(dt.timezone.utc)

        sub_earlier = UserSubscription(
            user_telegram_id=user_id,
            is_active=True,
            started_at=now - dt.timedelta(days=1),
            expires_at=now + dt.timedelta(days=earlier_days),
            granted_by_admin=True,
            stars_paid=0,
        )
        sub_later = UserSubscription(
            user_telegram_id=user_id,
            is_active=True,
            started_at=now - dt.timedelta(days=1),
            expires_at=now + dt.timedelta(days=later_days),
            granted_by_admin=True,
            stars_paid=0,
        )
        seed.add(sub_earlier)
        seed.add(sub_later)
        await seed.commit()

        earlier_id = sub_earlier.id
        later_id = sub_later.id
        original_earlier_expires = sub_earlier.expires_at
        original_later_expires = sub_later.expires_at

    # Call _grant_premium directly via a ReferralRepository instance
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        await repo._grant_premium(user_id, grant_days)
        await sess.commit()

    # Verify the row with the later expires_at was extended; the other unchanged
    async with session_factory() as check:
        def _tz(ts: dt.datetime) -> dt.datetime:
            return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)

        later_row = (
            await check.execute(
                select(UserSubscription).where(UserSubscription.id == later_id)
            )
        ).scalar_one()
        earlier_row = (
            await check.execute(
                select(UserSubscription).where(UserSubscription.id == earlier_id)
            )
        ).scalar_one()

        actual_later_expires  = _tz(later_row.expires_at)
        actual_earlier_expires = _tz(earlier_row.expires_at)

        # Normalise seed timestamps to UTC for comparison
        original_later_expires_utc  = _tz(original_later_expires)
        original_earlier_expires_utc = _tz(original_earlier_expires)

        expected_later_expires = original_later_expires_utc + dt.timedelta(days=grant_days)

        delta_later = abs((actual_later_expires - expected_later_expires).total_seconds())
        assert delta_later < 5, (
            f"_grant_premium did not extend the row with the later expires_at.\n"
            f"  sub_later (id={later_id}):\n"
            f"    original  : {original_later_expires_utc.isoformat()}\n"
            f"    expected  : {expected_later_expires.isoformat()}\n"
            f"    actual    : {actual_later_expires.isoformat()}\n"
            f"    delta     : {delta_later:.1f}s\n"
            "This means _grant_premium either picked the wrong row or did not extend at all."
        )

        delta_earlier = abs((actual_earlier_expires - original_earlier_expires_utc).total_seconds())
        assert delta_earlier < 5, (
            f"_grant_premium must leave the earlier subscription untouched, but it was modified.\n"
            f"  sub_earlier (id={earlier_id}):\n"
            f"    original  : {original_earlier_expires_utc.isoformat()}\n"
            f"    actual    : {actual_earlier_expires.isoformat()}\n"
            f"    delta     : {delta_earlier:.1f}s\n"
            "Only the row with the latest expires_at should be extended."
        )


# ---------------------------------------------------------------------------
# Mixed expired+active test: _grant_premium ignores the expired row and
# extends only the active one.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grant_premium_extends_active_row_leaves_expired_row_untouched(
    session_factory,
):
    """_grant_premium must extend only the active (non-expired) subscription when
    a user has both an expired row and a currently-active row.

    Scenario
    --------
    1. Seed a user with two subscription rows:
       - ``sub_expired`` : is_active=True, but expires_at is 5 days *in the past*
         (i.e. logically expired; the flag may still be True).
       - ``sub_active``  : is_active=True, expires_at is 15 days *in the future*.
    2. Call ``_grant_premium`` with 7 extra days.
    3. Assert that ``sub_active.expires_at`` moved forward by exactly 7 days.
    4. Assert that ``sub_expired.expires_at`` is completely unchanged.

    This exercises the ``UserSubscription.expires_at > now`` predicate in
    ``_grant_premium``.  If that filter were missing, the query could pick the
    expired row (e.g. when ordered differently) and extend the wrong one, leaving
    the user's visible subscription untouched.
    """
    import datetime as dt

    user_id = 9800
    grant_days = 7
    expired_days_ago = 5   # expires_at is this many days in the past
    active_days_ahead = 15  # expires_at is this many days in the future

    async with session_factory() as seed:
        await _seed_config(seed, milestones=[], referrer_reward_days=0, referee_reward_days=0)

        now = dt.datetime.now(dt.timezone.utc)

        sub_expired = UserSubscription(
            user_telegram_id=user_id,
            is_active=True,  # flag still set, but time has passed
            started_at=now - dt.timedelta(days=expired_days_ago + 30),
            expires_at=now - dt.timedelta(days=expired_days_ago),  # in the past
            granted_by_admin=True,
            stars_paid=0,
        )
        sub_active = UserSubscription(
            user_telegram_id=user_id,
            is_active=True,
            started_at=now - dt.timedelta(days=1),
            expires_at=now + dt.timedelta(days=active_days_ahead),  # in the future
            granted_by_admin=True,
            stars_paid=0,
        )
        seed.add(sub_expired)
        seed.add(sub_active)
        await seed.commit()

        expired_id = sub_expired.id
        active_id = sub_active.id
        original_expired_expires = sub_expired.expires_at
        original_active_expires = sub_active.expires_at

    # Call _grant_premium directly
    async with session_factory() as sess:
        repo = ReferralRepository(sess)
        await repo._grant_premium(user_id, grant_days)
        await sess.commit()

    # Verify only the active row was extended; the expired row is untouched
    async with session_factory() as check:
        def _tz(ts: dt.datetime) -> dt.datetime:
            return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)

        active_row = (
            await check.execute(
                select(UserSubscription).where(UserSubscription.id == active_id)
            )
        ).scalar_one()
        expired_row = (
            await check.execute(
                select(UserSubscription).where(UserSubscription.id == expired_id)
            )
        ).scalar_one()

        actual_active_expires  = _tz(active_row.expires_at)
        actual_expired_expires = _tz(expired_row.expires_at)

        original_active_expires_utc  = _tz(original_active_expires)
        original_expired_expires_utc = _tz(original_expired_expires)

        # Active row must be extended by exactly grant_days from its original expiry
        expected_active_expires = original_active_expires_utc + dt.timedelta(days=grant_days)
        delta_active = abs((actual_active_expires - expected_active_expires).total_seconds())
        assert delta_active < 5, (
            f"_grant_premium did not extend the active subscription.\n"
            f"  sub_active (id={active_id}):\n"
            f"    original  : {original_active_expires_utc.isoformat()}\n"
            f"    expected  : {expected_active_expires.isoformat()}\n"
            f"    actual    : {actual_active_expires.isoformat()}\n"
            f"    delta     : {delta_active:.1f}s\n"
            "_grant_premium must extend the active row (expires_at > now), "
            "not create a new subscription."
        )

        # Expired row must be completely untouched
        delta_expired = abs((actual_expired_expires - original_expired_expires_utc).total_seconds())
        assert delta_expired < 5, (
            f"_grant_premium modified the expired subscription — it must be left untouched.\n"
            f"  sub_expired (id={expired_id}):\n"
            f"    original  : {original_expired_expires_utc.isoformat()}\n"
            f"    actual    : {actual_expired_expires.isoformat()}\n"
            f"    delta     : {delta_expired:.1f}s\n"
            "Only the currently-active row (expires_at > now) should be extended."
        )
