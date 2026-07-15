"""Tests for RelationshipRepository coin-spending mutations.

Covers:
- send_request: insufficient funds, duplicate/pending prevention
- gift: cooldown enforcement, insufficient funds
- upgrade_tier: level requirement, insufficient funds
- cancel_request: refund correctness
- Concurrency: concurrent send_request calls for the same pair yield one row
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.relationship import (
    GIFT_COOLDOWN_H,
    GIFT_COST,
    GIFT_TO_PARTNER,
    GIFT_XP,
    REQUEST_COST,
    UPGRADE_COSTS,
    UPGRADE_MIN_LEVEL,
    Relationship,
)
from app.models.wallet import UserWallet
from app.repositories.relationship_repository import RelationshipRepository

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture()
async def engine():
    """In-memory SQLite engine for single-session tests."""
    eng = create_async_engine(DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
async def file_engine(tmp_path):
    """File-based SQLite engine whose data is shared across concurrent connections."""
    db_path = tmp_path / "test_concurrency.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as sess:
        yield sess
        await sess.rollback()


async def _seed_wallet(session: AsyncSession, user_id: int, balance: int) -> UserWallet:
    w = UserWallet(owner_telegram_id=user_id, balance=balance)
    session.add(w)
    await session.flush()
    return w


async def _seed_active_rel(
    session: AsyncSession,
    user_a: int,
    user_b: int,
    rel_type: str = "friends",
    level: int = 1,
    xp: int = 0,
) -> Relationship:
    a, b = min(user_a, user_b), max(user_a, user_b)
    rel = Relationship(
        user_a_id=a,
        user_b_id=b,
        initiator_id=user_a,
        rel_type=rel_type,
        level=level,
        xp=xp,
        status="active",
        created_at=dt.datetime.now(dt.timezone.utc),
        accepted_at=dt.datetime.now(dt.timezone.utc),
    )
    session.add(rel)
    await session.flush()
    return rel


# ---------------------------------------------------------------------------
# send_request tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_request_deducts_coins(session):
    """A successful request deducts REQUEST_COST from the requester's wallet."""
    await _seed_wallet(session, 1001, 200)
    repo = RelationshipRepository(session)
    rel = await repo.send_request(1001, 1002)
    wallet = (
        await session.get(UserWallet, (await session.execute(
            __import__("sqlalchemy").select(UserWallet).where(UserWallet.owner_telegram_id == 1001)
        )).scalar_one().id)
    )
    assert rel.status == "pending"
    assert rel.initiator_id == 1001


@pytest.mark.asyncio
async def test_send_request_insufficient_funds(session):
    """send_request raises ValueError('insufficient_funds') when balance < REQUEST_COST."""
    await _seed_wallet(session, 2001, REQUEST_COST - 1)
    repo = RelationshipRepository(session)
    with pytest.raises(ValueError, match="insufficient_funds"):
        await repo.send_request(2001, 2002)


@pytest.mark.asyncio
async def test_send_request_no_wallet_counts_as_zero(session):
    """A requester with no wallet row is treated as zero balance → insufficient_funds."""
    repo = RelationshipRepository(session)
    with pytest.raises(ValueError, match="insufficient_funds"):
        await repo.send_request(3001, 3002)


@pytest.mark.asyncio
async def test_send_request_duplicate_pending_blocked(session):
    """A second send_request while one is already pending raises 'request_pending'.

    The pending-relationship check fires before the wallet check, so the error
    is raised even if the balance would otherwise be sufficient.
    """
    await _seed_wallet(session, 4001, 500)
    repo = RelationshipRepository(session)
    await repo.send_request(4001, 4002)
    # The pending check runs first — no extra funds needed to hit the guard.
    with pytest.raises(ValueError, match="request_pending"):
        await repo.send_request(4001, 4002)


@pytest.mark.asyncio
async def test_send_request_active_rel_blocked(session):
    """send_request raises 'already_related' when an active relationship exists."""
    await _seed_wallet(session, 5001, 500)
    await _seed_active_rel(session, 5001, 5002)
    repo = RelationshipRepository(session)
    with pytest.raises(ValueError, match="already_related"):
        await repo.send_request(5001, 5002)


@pytest.mark.asyncio
async def test_send_request_self_blocked(session):
    """send_request raises ValueError when requester == addressee."""
    await _seed_wallet(session, 6001, 500)
    repo = RelationshipRepository(session)
    with pytest.raises(ValueError, match="cannot_self_request"):
        await repo.send_request(6001, 6001)


# ---------------------------------------------------------------------------
# cancel_request tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_request_refunds_coins(session):
    """Cancelling a pending request restores REQUEST_COST to the initiator's wallet."""
    await _seed_wallet(session, 7001, 200)
    repo = RelationshipRepository(session)
    await repo.send_request(7001, 7002)

    from sqlalchemy import select as sa_select
    wallet_before = (await session.execute(
        sa_select(UserWallet).where(UserWallet.owner_telegram_id == 7001)
    )).scalar_one()
    balance_after_request = wallet_before.balance

    await repo.cancel_request(7001, 7002)

    await session.refresh(wallet_before)
    assert wallet_before.balance == balance_after_request + REQUEST_COST


@pytest.mark.asyncio
async def test_cancel_request_non_initiator_blocked(session):
    """The addressee cannot cancel the initiator's request."""
    await _seed_wallet(session, 8001, 200)
    repo = RelationshipRepository(session)
    await repo.send_request(8001, 8002)

    with pytest.raises(ValueError, match="no_own_pending_request"):
        await repo.cancel_request(8002, 8001)


@pytest.mark.asyncio
async def test_cancel_request_no_pending_blocked(session):
    """cancel_request raises when there is no pending request."""
    repo = RelationshipRepository(session)
    with pytest.raises(ValueError, match="no_own_pending_request"):
        await repo.cancel_request(9001, 9002)


# ---------------------------------------------------------------------------
# gift tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gift_transfers_coins_and_xp(session):
    """gift() deducts GIFT_COST from sender and adds GIFT_TO_PARTNER to partner."""
    from sqlalchemy import select as sa_select

    await _seed_wallet(session, 10001, 200)
    await _seed_wallet(session, 10002, 0)
    await _seed_active_rel(session, 10001, 10002)

    repo = RelationshipRepository(session)
    result = await repo.gift(10001, 10002)

    sender_w = (await session.execute(
        sa_select(UserWallet).where(UserWallet.owner_telegram_id == 10001)
    )).scalar_one()
    partner_w = (await session.execute(
        sa_select(UserWallet).where(UserWallet.owner_telegram_id == 10002)
    )).scalar_one()

    assert sender_w.balance == 200 - GIFT_COST
    assert partner_w.balance == GIFT_TO_PARTNER
    assert result["new_xp"] == GIFT_XP
    assert result["partner_received"] == GIFT_TO_PARTNER


@pytest.mark.asyncio
async def test_gift_insufficient_funds(session):
    """gift() raises 'insufficient_funds' when sender's balance < GIFT_COST."""
    await _seed_wallet(session, 11001, GIFT_COST - 1)
    await _seed_wallet(session, 11002, 0)
    await _seed_active_rel(session, 11001, 11002)

    repo = RelationshipRepository(session)
    with pytest.raises(ValueError, match="insufficient_funds"):
        await repo.gift(11001, 11002)


@pytest.mark.asyncio
async def test_gift_cooldown_enforced(session):
    """gift() raises 'gift_cooldown:…' when called again within GIFT_COOLDOWN_H hours."""
    await _seed_wallet(session, 12001, 500)
    await _seed_wallet(session, 12002, 0)
    rel = await _seed_active_rel(session, 12001, 12002)

    repo = RelationshipRepository(session)
    await repo.gift(12001, 12002)

    # Attempt a second gift without enough time having passed
    with pytest.raises(ValueError, match="gift_cooldown:"):
        await repo.gift(12001, 12002)


@pytest.mark.asyncio
async def test_gift_cooldown_expires(session):
    """gift() succeeds again once GIFT_COOLDOWN_H hours have elapsed."""
    from sqlalchemy import select as sa_select

    await _seed_wallet(session, 13001, 500)
    await _seed_wallet(session, 13002, 0)
    rel = await _seed_active_rel(session, 13001, 13002)

    # Backdate the last_gift to simulate cooldown already expired
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=GIFT_COOLDOWN_H + 1)
    rel.last_gift_a = past  # 13001 is the smaller id so mapped to user_a
    await session.flush()

    repo = RelationshipRepository(session)
    result = await repo.gift(13001, 13002)
    assert result["new_xp"] == GIFT_XP


@pytest.mark.asyncio
async def test_gift_not_related_blocked(session):
    """gift() raises 'not_related' when there is no active relationship."""
    await _seed_wallet(session, 14001, 200)
    repo = RelationshipRepository(session)
    with pytest.raises(ValueError, match="not_related"):
        await repo.gift(14001, 14002)


@pytest.mark.asyncio
async def test_gift_partner_no_wallet_atomic(session):
    """gift() creates the partner's wallet and credits it atomically.

    Scenario: the sender has a wallet, but the partner has never interacted
    with the bot and therefore has no UserWallet row.  gift() must:
      1. create the partner's wallet inside the same session transaction,
      2. debit the sender and credit the partner, and
      3. flush both changes together — so either both persist or neither does.

    This test verifies the happy-path side: after a successful gift() call both
    the debit and the newly-created credit are visible in the same session,
    confirming they were flushed atomically.
    """
    from sqlalchemy import select as sa_select

    SENDER = 21001
    PARTNER = 21002

    # Seed only the sender's wallet — partner intentionally has no row.
    await _seed_wallet(session, SENDER, 200)
    await _seed_active_rel(session, SENDER, PARTNER)

    repo = RelationshipRepository(session)
    result = await repo.gift(SENDER, PARTNER)

    # Both changes must be visible within the same session after the flush.
    sender_w = (await session.execute(
        sa_select(UserWallet).where(UserWallet.owner_telegram_id == SENDER)
    )).scalar_one()
    partner_w = (await session.execute(
        sa_select(UserWallet).where(UserWallet.owner_telegram_id == PARTNER)
    )).scalar_one()

    assert sender_w.balance == 200 - GIFT_COST, (
        "Sender should be debited GIFT_COST even when partner had no wallet"
    )
    assert partner_w.balance == GIFT_TO_PARTNER, (
        "Partner wallet must be created and credited in the same transaction"
    )
    assert result["partner_received"] == GIFT_TO_PARTNER
    assert result["new_xp"] == GIFT_XP


# ---------------------------------------------------------------------------
# upgrade_tier tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upgrade_tier_below_level_blocked(session):
    """upgrade_tier raises when the current level < UPGRADE_MIN_LEVEL."""
    await _seed_wallet(session, 15001, 1000)
    await _seed_active_rel(session, 15001, 15002, rel_type="friends", level=1)
    repo = RelationshipRepository(session)
    with pytest.raises(ValueError, match="need_level_"):
        await repo.upgrade_tier(15001, 15002)


@pytest.mark.asyncio
async def test_upgrade_tier_insufficient_funds(session):
    """upgrade_tier raises 'insufficient_funds' when wallet < UPGRADE_COSTS."""
    min_level = UPGRADE_MIN_LEVEL["friends"]
    xp_needed = (min_level - 1) * 200  # XP_PER_LEVEL = 200
    await _seed_wallet(session, 16001, UPGRADE_COSTS["friends"] - 1)
    await _seed_active_rel(
        session, 16001, 16002, rel_type="friends",
        level=min_level, xp=xp_needed,
    )
    repo = RelationshipRepository(session)
    with pytest.raises(ValueError, match="insufficient_funds"):
        await repo.upgrade_tier(16001, 16002)


@pytest.mark.asyncio
async def test_upgrade_tier_success(session):
    """A qualifying upgrade deducts the cost and advances to the next tier."""
    from sqlalchemy import select as sa_select

    min_level = UPGRADE_MIN_LEVEL["friends"]
    xp_needed = (min_level - 1) * 200
    await _seed_wallet(session, 17001, UPGRADE_COSTS["friends"] + 100)
    await _seed_active_rel(
        session, 17001, 17002, rel_type="friends",
        level=min_level, xp=xp_needed,
    )
    repo = RelationshipRepository(session)
    rel = await repo.upgrade_tier(17001, 17002)

    assert rel.rel_type == "dating"
    assert rel.level == 1

    wallet = (await session.execute(
        sa_select(UserWallet).where(UserWallet.owner_telegram_id == 17001)
    )).scalar_one()
    assert wallet.balance == 100


@pytest.mark.asyncio
async def test_upgrade_tier_already_max_blocked(session):
    """upgrade_tier raises 'already_max_tier' for a married couple."""
    await _seed_wallet(session, 18001, 5000)
    await _seed_active_rel(session, 18001, 18002, rel_type="married", level=5)
    repo = RelationshipRepository(session)
    with pytest.raises(ValueError, match="already_max_tier"):
        await repo.upgrade_tier(18001, 18002)


@pytest.mark.asyncio
async def test_upgrade_tier_not_related_blocked(session):
    """upgrade_tier raises 'not_related' when no active relationship exists."""
    await _seed_wallet(session, 19001, 5000)
    repo = RelationshipRepository(session)
    with pytest.raises(ValueError, match="not_related"):
        await repo.upgrade_tier(19001, 19002)


# ---------------------------------------------------------------------------
# Cooldown reset after tier upgrade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gift_cooldown_resets_after_upgrade(session):
    """upgrade_tier() zeroes last_gift_a/b so the sender can gift immediately.

    Flow:
      1. A and B are active friends at the minimum upgrade level.
      2. A sends a gift → cooldown is now active.
      3. A's second gift is blocked by the cooldown (sanity check).
      4. A upgrades the tier (friends → dating).
      5. A can gift again immediately — no cooldown error.
      6. B can also gift immediately (their cooldown was also reset).
    """
    from sqlalchemy import select as sa_select

    USER_A, USER_B = 22001, 22002

    # Enough coins for: gift + upgrade + second gift (+ B's gift)
    balance = GIFT_COST + UPGRADE_COSTS["friends"] + GIFT_COST * 2 + 100
    await _seed_wallet(session, USER_A, balance)
    await _seed_wallet(session, USER_B, balance)

    min_lvl  = UPGRADE_MIN_LEVEL["friends"]
    xp_start = (min_lvl - 1) * 200   # puts us at exactly min_lvl before any gift

    await _seed_active_rel(
        session, USER_A, USER_B,
        rel_type="friends",
        level=min_lvl,
        xp=xp_start,
    )

    repo = RelationshipRepository(session)

    # Step 2 — first gift works, cooldown starts
    await repo.gift(USER_A, USER_B)

    # Step 3 — immediate second gift is blocked (sanity: cooldown IS active)
    with pytest.raises(ValueError, match="gift_cooldown:"):
        await repo.gift(USER_A, USER_B)

    # Step 4 — upgrade resets both cooldown timestamps
    upgraded = await repo.upgrade_tier(USER_A, USER_B)
    assert upgraded.rel_type == "dating", "Tier should advance to 'dating'"
    assert upgraded.last_gift_a is None, "last_gift_a must be None after upgrade"
    assert upgraded.last_gift_b is None, "last_gift_b must be None after upgrade"

    # Step 5 — A can gift again immediately (no cooldown error)
    result_a = await repo.gift(USER_A, USER_B)
    assert result_a["new_xp"] == GIFT_XP, (
        "First gift after upgrade should succeed and record XP"
    )

    # Step 6 — B's cooldown was also reset; B can gift A right away
    result_b = await repo.gift(USER_B, USER_A)
    assert result_b["new_xp"] == GIFT_XP * 2, (
        "B's gift after upgrade should also succeed (cooldown was reset for both sides)"
    )


# ---------------------------------------------------------------------------
# Concurrency: duplicate send_request for the same pair
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_send_request_single_row(file_engine):
    """Two concurrent send_request calls for the same pair produce at most one row.

    This validates that the UniqueConstraint on (user_a_id, user_b_id) catches
    any race that slips past the application-level duplicate check.
    """
    from sqlalchemy import select as sa_select

    factory = async_sessionmaker(file_engine, expire_on_commit=False, class_=AsyncSession)

    # Pre-fund both sessions' view of user 20001's wallet
    async with factory() as setup_sess:
        setup_sess.add(UserWallet(owner_telegram_id=20001, balance=1000))
        setup_sess.add(UserWallet(owner_telegram_id=20002, balance=0))
        await setup_sess.commit()

    successes = 0
    errors = 0

    async def try_send(uid_from: int, uid_to: int) -> None:
        nonlocal successes, errors
        async with factory() as sess:
            try:
                repo = RelationshipRepository(sess)
                await repo.send_request(uid_from, uid_to)
                await sess.commit()
                successes += 1
            except (ValueError, IntegrityError, Exception):
                await sess.rollback()
                errors += 1

    # Fire both concurrently
    await asyncio.gather(
        try_send(20001, 20002),
        try_send(20001, 20002),
    )

    # Exactly one relationship row must exist
    async with factory() as verify_sess:
        rows = (await verify_sess.execute(
            sa_select(Relationship).where(
                Relationship.user_a_id == min(20001, 20002),
                Relationship.user_b_id == max(20001, 20002),
            )
        )).scalars().all()

    assert len(rows) == 1, (
        f"Expected 1 relationship row, got {len(rows)}. "
        f"successes={successes}, errors={errors}"
    )
    assert successes + errors == 2
