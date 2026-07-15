"""Tests for relationship XP bonus edge cases.

Covers:
- get_active_tier: no relationship, pending, active friends/dating/married, broken
- rel_xp_multiplier: correct multiplier returned per tier and for None
- feed/play/cuddle: xp_gained reflects the active relationship tier multiplier
- Tier upgrade: bonus updates to new tier immediately after upgrade
- Break-up: bonus drops to 1.0 (no relationship) after break_rel
"""
from __future__ import annotations

import datetime as dt
import math

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.pet import ChatPet
from app.models.relationship import (
    REL_XP_BONUS,
    TIER_ORDER,
    UPGRADE_COSTS,
    UPGRADE_MIN_LEVEL,
    Relationship,
)
from app.models.wallet import UserWallet
from app.repositories.pet_repository import CUDDLE_XP, FEED_XP, PLAY_XP, PetRepository
from app.repositories.relationship_repository import RelationshipRepository

# ---------------------------------------------------------------------------
# Engine / session fixtures
# ---------------------------------------------------------------------------

DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture()
async def engine():
    eng = create_async_engine(DATABASE_URL, echo=False)
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


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

async def _seed_wallet(session: AsyncSession, user_id: int, balance: int) -> UserWallet:
    w = UserWallet(owner_telegram_id=user_id, balance=balance)
    session.add(w)
    await session.flush()
    return w


async def _seed_rel(
    session: AsyncSession,
    user_a: int,
    user_b: int,
    *,
    status: str = "active",
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
        status=status,
        created_at=dt.datetime.now(dt.timezone.utc),
        accepted_at=dt.datetime.now(dt.timezone.utc) if status == "active" else None,
    )
    session.add(rel)
    await session.flush()
    return rel


async def _seed_pet(
    session: AsyncSession,
    owner_id: int,
    chat_id: int,
    *,
    personality: str = "brave",  # brave avoids streak-death complications
) -> ChatPet:
    now = dt.datetime.now(dt.timezone.utc)
    # Backdate born_at so cuddle/play cooldowns don't interfere
    past = now - dt.timedelta(hours=10)
    pet = ChatPet(
        owner_telegram_id=owner_id,
        chat_id=chat_id,
        pet_name="Тестик",
        species="cat",
        interlocutor_name="Partner",
        personality=personality,
        is_alive=True,
        born_at=past,
        mood=80,
    )
    session.add(pet)
    await session.flush()
    return pet


# ---------------------------------------------------------------------------
# get_active_tier tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_tier_no_relationship(session):
    """Returns None when no relationship row exists."""
    repo = RelationshipRepository(session)
    tier = await repo.get_active_tier(1001, 1002)
    assert tier is None


@pytest.mark.asyncio
async def test_get_active_tier_pending_request(session):
    """Returns None for a pending (not yet accepted) request."""
    await _seed_rel(session, 2001, 2002, status="pending", rel_type="friends")
    repo = RelationshipRepository(session)
    tier = await repo.get_active_tier(2001, 2002)
    assert tier is None


@pytest.mark.asyncio
async def test_get_active_tier_active_friends(session):
    """Returns 'friends' for an active friends relationship."""
    await _seed_rel(session, 3001, 3002, status="active", rel_type="friends")
    repo = RelationshipRepository(session)
    tier = await repo.get_active_tier(3001, 3002)
    assert tier == "friends"


@pytest.mark.asyncio
async def test_get_active_tier_active_dating(session):
    """Returns 'dating' for an active dating relationship."""
    await _seed_rel(session, 4001, 4002, status="active", rel_type="dating")
    repo = RelationshipRepository(session)
    tier = await repo.get_active_tier(4001, 4002)
    assert tier == "dating"


@pytest.mark.asyncio
async def test_get_active_tier_active_married(session):
    """Returns 'married' for an active marriage."""
    await _seed_rel(session, 5001, 5002, status="active", rel_type="married")
    repo = RelationshipRepository(session)
    tier = await repo.get_active_tier(5001, 5002)
    assert tier == "married"


@pytest.mark.asyncio
async def test_get_active_tier_broken_relationship(session):
    """Returns None after a relationship is broken."""
    await _seed_rel(session, 6001, 6002, status="broken", rel_type="married")
    repo = RelationshipRepository(session)
    tier = await repo.get_active_tier(6001, 6002)
    assert tier is None


@pytest.mark.asyncio
async def test_get_active_tier_pair_order_symmetric(session):
    """get_active_tier returns the same result regardless of argument order."""
    await _seed_rel(session, 7001, 7002, status="active", rel_type="dating")
    repo = RelationshipRepository(session)
    assert await repo.get_active_tier(7001, 7002) == "dating"
    assert await repo.get_active_tier(7002, 7001) == "dating"


# ---------------------------------------------------------------------------
# rel_xp_multiplier tests
# ---------------------------------------------------------------------------


def test_rel_xp_multiplier_none():
    """No relationship → multiplier is 1.0 (no bonus)."""
    repo = RelationshipRepository.__new__(RelationshipRepository)
    assert repo.rel_xp_multiplier(None) == 1.0


def test_rel_xp_multiplier_friends():
    """Friends tier returns the configured bonus (1.05)."""
    repo = RelationshipRepository.__new__(RelationshipRepository)
    assert repo.rel_xp_multiplier("friends") == REL_XP_BONUS["friends"]


def test_rel_xp_multiplier_dating():
    """Dating tier returns the configured bonus (1.10)."""
    repo = RelationshipRepository.__new__(RelationshipRepository)
    assert repo.rel_xp_multiplier("dating") == REL_XP_BONUS["dating"]


def test_rel_xp_multiplier_married():
    """Married tier returns the configured bonus (1.15)."""
    repo = RelationshipRepository.__new__(RelationshipRepository)
    assert repo.rel_xp_multiplier("married") == REL_XP_BONUS["married"]


def test_rel_xp_multiplier_unknown_tier():
    """An unrecognised tier string falls back to 1.0."""
    repo = RelationshipRepository.__new__(RelationshipRepository)
    assert repo.rel_xp_multiplier("unknown") == 1.0


def test_rel_xp_bonus_tiers_increase_with_tier():
    """Each tier's bonus is strictly greater than the previous one."""
    bonuses = [REL_XP_BONUS[t] for t in TIER_ORDER]
    for i in range(1, len(bonuses)):
        assert bonuses[i] > bonuses[i - 1], (
            f"Expected {TIER_ORDER[i]} bonus > {TIER_ORDER[i - 1]} bonus"
        )


# ---------------------------------------------------------------------------
# feed XP tests with relationship bonus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_xp_no_relationship(session):
    """Feed with no relationship applies 1.0 multiplier (base XP only)."""
    owner, partner = 20001, 20002
    await _seed_wallet(session, owner, 500)
    pet = await _seed_pet(session, owner, partner)

    repo = PetRepository(session)
    result = await repo.feed(owner, pet.id)

    expected_xp = round(FEED_XP * 1.0)  # kibble xp_mult=1.0, rel_mult=1.0
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] is None
    assert result["rel_bonus"] == 1.0


@pytest.mark.asyncio
async def test_feed_xp_friends_bonus(session):
    """Feed with active friends relationship applies the friends multiplier."""
    owner, partner = 21001, 21002
    await _seed_wallet(session, owner, 500)
    await _seed_rel(session, owner, partner, status="active", rel_type="friends")
    pet = await _seed_pet(session, owner, partner)

    repo = PetRepository(session)
    result = await repo.feed(owner, pet.id)

    rel_mult = REL_XP_BONUS["friends"]
    expected_xp = round(FEED_XP * 1.0 * rel_mult)
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] == "friends"
    assert result["rel_bonus"] == rel_mult


@pytest.mark.asyncio
async def test_feed_xp_dating_bonus(session):
    """Feed with active dating relationship applies the dating multiplier."""
    owner, partner = 22001, 22002
    await _seed_wallet(session, owner, 500)
    await _seed_rel(session, owner, partner, status="active", rel_type="dating")
    pet = await _seed_pet(session, owner, partner)

    repo = PetRepository(session)
    result = await repo.feed(owner, pet.id)

    rel_mult = REL_XP_BONUS["dating"]
    expected_xp = round(FEED_XP * 1.0 * rel_mult)
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] == "dating"


@pytest.mark.asyncio
async def test_feed_xp_married_bonus(session):
    """Feed with active marriage applies the married multiplier."""
    owner, partner = 23001, 23002
    await _seed_wallet(session, owner, 500)
    await _seed_rel(session, owner, partner, status="active", rel_type="married")
    pet = await _seed_pet(session, owner, partner)

    repo = PetRepository(session)
    result = await repo.feed(owner, pet.id)

    rel_mult = REL_XP_BONUS["married"]
    expected_xp = round(FEED_XP * 1.0 * rel_mult)
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] == "married"


@pytest.mark.asyncio
async def test_feed_xp_pending_relationship_no_bonus(session):
    """A pending (unaccepted) request does not grant any XP bonus."""
    owner, partner = 24001, 24002
    await _seed_wallet(session, owner, 500)
    await _seed_rel(session, owner, partner, status="pending", rel_type="friends")
    pet = await _seed_pet(session, owner, partner)

    repo = PetRepository(session)
    result = await repo.feed(owner, pet.id)

    expected_xp = round(FEED_XP * 1.0)
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] is None


# ---------------------------------------------------------------------------
# play XP tests with relationship bonus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_play_xp_no_relationship(session):
    """Play with no relationship applies no bonus."""
    owner, partner = 30001, 30002
    pet = await _seed_pet(session, owner, partner)

    repo = PetRepository(session)
    result = await repo.play(owner, pet.id)

    # playful personality doubles mood; base PLAY_XP unchanged
    expected_xp = round(PLAY_XP * 1.0)
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] is None


@pytest.mark.asyncio
async def test_play_xp_friends_bonus(session):
    """Play with active friends relationship applies 1.05×."""
    owner, partner = 31001, 31002
    await _seed_rel(session, owner, partner, status="active", rel_type="friends")
    pet = await _seed_pet(session, owner, partner)

    repo = PetRepository(session)
    result = await repo.play(owner, pet.id)

    rel_mult = REL_XP_BONUS["friends"]
    expected_xp = round(PLAY_XP * rel_mult)
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] == "friends"


@pytest.mark.asyncio
async def test_play_xp_married_bonus(session):
    """Play with active marriage applies 1.15×."""
    owner, partner = 32001, 32002
    await _seed_rel(session, owner, partner, status="active", rel_type="married")
    pet = await _seed_pet(session, owner, partner)

    repo = PetRepository(session)
    result = await repo.play(owner, pet.id)

    rel_mult = REL_XP_BONUS["married"]
    expected_xp = round(PLAY_XP * rel_mult)
    assert result["xp_gained"] == expected_xp


# ---------------------------------------------------------------------------
# cuddle XP tests with relationship bonus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cuddle_xp_no_relationship(session):
    """Cuddle with no relationship applies no bonus."""
    owner, partner = 40001, 40002
    pet = await _seed_pet(session, owner, partner)

    repo = PetRepository(session)
    result = await repo.cuddle(owner, pet.id)

    expected_xp = round(CUDDLE_XP * 1.0)
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] is None


@pytest.mark.asyncio
async def test_cuddle_xp_friends_bonus(session):
    """Cuddle with active friends relationship applies 1.05×."""
    owner, partner = 41001, 41002
    await _seed_rel(session, owner, partner, status="active", rel_type="friends")
    pet = await _seed_pet(session, owner, partner)

    repo = PetRepository(session)
    result = await repo.cuddle(owner, pet.id)

    rel_mult = REL_XP_BONUS["friends"]
    expected_xp = round(CUDDLE_XP * rel_mult)
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] == "friends"


@pytest.mark.asyncio
async def test_cuddle_xp_married_bonus(session):
    """Cuddle with active marriage applies 1.15×."""
    owner, partner = 42001, 42002
    await _seed_rel(session, owner, partner, status="active", rel_type="married")
    pet = await _seed_pet(session, owner, partner)

    repo = PetRepository(session)
    result = await repo.cuddle(owner, pet.id)

    rel_mult = REL_XP_BONUS["married"]
    expected_xp = round(CUDDLE_XP * rel_mult)
    assert result["xp_gained"] == expected_xp


# ---------------------------------------------------------------------------
# Tier upgrade: bonus changes immediately
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_xp_updates_after_tier_upgrade(session):
    """After upgrading friends → dating, the next feed uses the dating multiplier."""
    owner, partner = 50001, 50002
    # Wallet needs to cover: feed cost (20) + upgrade cost
    upgrade_cost = UPGRADE_COSTS["friends"]
    await _seed_wallet(session, owner, upgrade_cost + 200)

    min_level = UPGRADE_MIN_LEVEL["friends"]
    # xp must put rel at exactly min_level within friends tier
    from app.models.relationship import XP_PER_LEVEL
    rel_xp = (min_level - 1) * XP_PER_LEVEL

    rel = await _seed_rel(
        session, owner, partner,
        status="active", rel_type="friends",
        level=min_level, xp=rel_xp,
    )
    pet = await _seed_pet(session, owner, partner)

    rel_repo = RelationshipRepository(session)
    upgraded_rel = await rel_repo.upgrade_tier(owner, partner)
    assert upgraded_rel.rel_type == "dating"

    # Feed after upgrade — should use dating multiplier
    pet_repo = PetRepository(session)
    result = await pet_repo.feed(owner, pet.id)

    rel_mult = REL_XP_BONUS["dating"]
    expected_xp = round(FEED_XP * 1.0 * rel_mult)
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] == "dating"


@pytest.mark.asyncio
async def test_play_xp_updates_after_tier_upgrade_to_married(session):
    """After upgrading dating → married, the next play uses the married multiplier."""
    owner, partner = 51001, 51002
    upgrade_cost = UPGRADE_COSTS["dating"]
    await _seed_wallet(session, owner, upgrade_cost + 200)

    from app.models.relationship import XP_PER_LEVEL
    min_level = UPGRADE_MIN_LEVEL["dating"]
    rel_xp = (min_level - 1) * XP_PER_LEVEL

    rel = await _seed_rel(
        session, owner, partner,
        status="active", rel_type="dating",
        level=min_level, xp=rel_xp,
    )
    pet = await _seed_pet(session, owner, partner)

    rel_repo = RelationshipRepository(session)
    upgraded_rel = await rel_repo.upgrade_tier(owner, partner)
    assert upgraded_rel.rel_type == "married"

    pet_repo = PetRepository(session)
    result = await pet_repo.play(owner, pet.id)

    rel_mult = REL_XP_BONUS["married"]
    expected_xp = round(PLAY_XP * rel_mult)
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] == "married"


# ---------------------------------------------------------------------------
# Break-up: bonus drops to 1.0 immediately
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_xp_no_bonus_after_breakup(session):
    """After a break-up, feed XP reverts to the base 1.0 multiplier."""
    owner, partner = 60001, 60002
    await _seed_wallet(session, owner, 500)
    await _seed_rel(session, owner, partner, status="active", rel_type="married")
    pet = await _seed_pet(session, owner, partner)

    # Verify the bonus was active before breaking up
    pet_repo = PetRepository(session)
    result_before = await pet_repo.feed(owner, pet.id)
    assert result_before["rel_tier"] == "married"
    assert result_before["rel_bonus"] == REL_XP_BONUS["married"]

    # Break the relationship
    rel_repo = RelationshipRepository(session)
    await rel_repo.break_rel(owner, partner)

    # Backdate last_fed_at so the cooldown has expired for the next feed call
    from sqlalchemy import select as sa_select
    pet_row = (await session.execute(
        sa_select(ChatPet).where(
            ChatPet.owner_telegram_id == owner,
            ChatPet.chat_id == partner,
        )
    )).scalar_one()
    pet_row.last_fed_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=25)
    await session.flush()

    result_after = await pet_repo.feed(owner, pet.id)
    expected_xp = round(FEED_XP * 1.0)
    assert result_after["xp_gained"] == expected_xp
    assert result_after["rel_tier"] is None
    assert result_after["rel_bonus"] == 1.0


@pytest.mark.asyncio
async def test_cuddle_xp_no_bonus_after_breakup(session):
    """After a break-up, cuddle XP reverts to the base 1.0 multiplier."""
    owner, partner = 61001, 61002
    await _seed_rel(session, owner, partner, status="active", rel_type="dating")
    pet = await _seed_pet(session, owner, partner)

    rel_repo = RelationshipRepository(session)
    await rel_repo.break_rel(owner, partner)

    # Verify tier is gone
    tier = await rel_repo.get_active_tier(owner, partner)
    assert tier is None

    pet_repo = PetRepository(session)
    result = await pet_repo.cuddle(owner, pet.id)

    expected_xp = round(CUDDLE_XP * 1.0)
    assert result["xp_gained"] == expected_xp
    assert result["rel_tier"] is None
