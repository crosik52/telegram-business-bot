"""Integration tests: marriage coin bonus stops after break-up.

Scenario:
  - User A and User B are married (active marriage).
  - count_marriages(A) → 1, marriage_bonus = MARRIAGE_DAILY_BONUS * 1 > 0.
  - User B calls break_rel.
  - count_marriages(A) → 0, marriage_bonus = 0.
  - A subsequent claim_daily for A carries premium_bonus=0 from the marriage.

These tests use RelationshipRepository and WalletRepository directly (no HTTP
layer / auth) so they run fully in-process against an in-memory SQLite DB.
"""
from __future__ import annotations

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.relationship import MARRIAGE_DAILY_BONUS, Relationship
from app.models.wallet import UserWallet
from app.repositories.relationship_repository import RelationshipRepository
from app.repositories.wallet_repository import WalletRepository

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

async def _seed_wallet(session: AsyncSession, user_id: int, balance: int = 1000) -> UserWallet:
    w = UserWallet(owner_telegram_id=user_id, balance=balance)
    session.add(w)
    await session.flush()
    return w


async def _seed_marriage(
    session: AsyncSession,
    user_a: int,
    user_b: int,
) -> Relationship:
    """Seed an active marriage between user_a and user_b."""
    a, b = min(user_a, user_b), max(user_a, user_b)
    now = dt.datetime.now(dt.timezone.utc)
    rel = Relationship(
        user_a_id=a,
        user_b_id=b,
        initiator_id=user_a,
        rel_type="married",
        level=1,
        xp=0,
        status="active",
        created_at=now,
        accepted_at=now,
    )
    session.add(rel)
    await session.flush()
    return rel


# ---------------------------------------------------------------------------
# count_marriages tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_marriages_no_relationship(session):
    """count_marriages returns 0 when the user has no relationships at all."""
    repo = RelationshipRepository(session)
    assert await repo.count_marriages(9001) == 0


@pytest.mark.asyncio
async def test_count_marriages_active_marriage(session):
    """count_marriages returns 1 for a user in one active marriage."""
    user_a, user_b = 9100, 9101
    await _seed_marriage(session, user_a, user_b)
    repo = RelationshipRepository(session)
    assert await repo.count_marriages(user_a) == 1
    assert await repo.count_marriages(user_b) == 1


@pytest.mark.asyncio
async def test_count_marriages_pending_not_counted(session):
    """A pending marriage request does not count toward the marriage bonus."""
    user_a, user_b = 9200, 9201
    a, b = min(user_a, user_b), max(user_a, user_b)
    rel = Relationship(
        user_a_id=a,
        user_b_id=b,
        initiator_id=user_a,
        rel_type="married",
        level=1,
        xp=0,
        status="pending",
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    session.add(rel)
    await session.flush()

    repo = RelationshipRepository(session)
    assert await repo.count_marriages(user_a) == 0


@pytest.mark.asyncio
async def test_count_marriages_non_marriage_tier_not_counted(session):
    """Active friends/dating relationships do not count as marriages."""
    # Use distinct pairs per tier to avoid the UniqueConstraint on (user_a_id, user_b_id)
    pairs_and_tiers = [
        (9300, 9301, "friends"),
        (9302, 9303, "dating"),
    ]
    repo = RelationshipRepository(session)
    for user_a, user_b, tier in pairs_and_tiers:
        a, b = min(user_a, user_b), max(user_a, user_b)
        rel = Relationship(
            user_a_id=a,
            user_b_id=b,
            initiator_id=user_a,
            rel_type=tier,
            level=1,
            xp=0,
            status="active",
            created_at=dt.datetime.now(dt.timezone.utc),
            accepted_at=dt.datetime.now(dt.timezone.utc),
        )
        session.add(rel)
        await session.flush()

        assert await repo.count_marriages(user_a) == 0, f"{tier} should not count as marriage"


# ---------------------------------------------------------------------------
# Main scenario: bonus zeroes out after break-up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marriage_bonus_is_zero_after_breakup(session):
    """Core invariant: count_marriages drops to 0 the moment break_rel is called.

    Flow:
      1. A and B are married → count_marriages(A) == 1
      2. B calls break_rel  → count_marriages(A) == 0 immediately
      3. Computed marriage_bonus for A's next daily claim is therefore 0.
    """
    user_a, user_b = 8001, 8002

    await _seed_marriage(session, user_a, user_b)
    rel_repo = RelationshipRepository(session)

    # Step 1 — bonus is active
    marriages_before = await rel_repo.count_marriages(user_a)
    bonus_before = MARRIAGE_DAILY_BONUS * marriages_before
    assert marriages_before == 1, "Expected exactly 1 active marriage before break-up"
    assert bonus_before == MARRIAGE_DAILY_BONUS, "Marriage bonus should equal MARRIAGE_DAILY_BONUS"

    # Step 2 — partner breaks the relationship
    await rel_repo.break_rel(user_b, user_a)

    # Step 3 — bonus is gone for user A
    marriages_after = await rel_repo.count_marriages(user_a)
    bonus_after = MARRIAGE_DAILY_BONUS * marriages_after
    assert marriages_after == 0, "count_marriages must be 0 after break-up"
    assert bonus_after == 0, "Marriage bonus must be 0 after break-up"


@pytest.mark.asyncio
async def test_marriage_bonus_in_daily_claim_drops_to_zero_after_breakup(session):
    """claim_daily called after a break-up receives premium_bonus=0 (marriage part).

    This test wires RelationshipRepository + WalletRepository together the same
    way the route handler does, confirming the end-to-end coin reward is correct.

    Steps:
      1. Seed marriage, seed wallet for A.
      2. Simulate route logic: compute marriage_bonus, call claim_daily.
         Verify earned > base (marriage bonus was included).
      3. B breaks the relationship.
      4. Backdate wallet so A can claim again.
      5. Recompute marriage_bonus (should be 0), call claim_daily again.
         Verify earned == base (no marriage bonus).
    """
    user_a, user_b = 8100, 8101
    await _seed_wallet(session, user_a)
    await _seed_marriage(session, user_a, user_b)

    rel_repo    = RelationshipRepository(session)
    wallet_repo = WalletRepository(session)

    # ── Claim 1: while married ────────────────────────────────────────────
    marriages_1     = await rel_repo.count_marriages(user_a)
    marriage_bonus_1 = MARRIAGE_DAILY_BONUS * marriages_1
    assert marriage_bonus_1 > 0, "Should have a positive marriage bonus before break-up"

    result_1 = await wallet_repo.claim_daily(
        user_a,
        streak_days=0,
        premium_multiplier=1.0,
        premium_bonus=marriage_bonus_1,
    )
    assert result_1.premium_bonus == marriage_bonus_1
    assert result_1.earned > result_1.base, (
        "Earned must exceed base when marriage bonus is applied"
    )

    # ── Break-up ─────────────────────────────────────────────────────────
    await rel_repo.break_rel(user_b, user_a)

    # ── Backdate wallet so A can claim again ──────────────────────────────
    from sqlalchemy import select as _select
    wallet_row = (
        await session.execute(
            _select(UserWallet).where(UserWallet.owner_telegram_id == user_a)
        )
    ).scalar_one()
    wallet_row.last_daily_claim = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=25)
    )
    await session.flush()

    # ── Claim 2: after break-up ───────────────────────────────────────────
    marriages_2      = await rel_repo.count_marriages(user_a)
    marriage_bonus_2 = MARRIAGE_DAILY_BONUS * marriages_2
    assert marriage_bonus_2 == 0, "Marriage bonus must be 0 after break-up"

    result_2 = await wallet_repo.claim_daily(
        user_a,
        streak_days=0,
        premium_multiplier=1.0,
        premium_bonus=marriage_bonus_2,
    )
    assert result_2.premium_bonus == 0
    assert result_2.earned == result_2.base, (
        "Earned must equal base when there is no marriage bonus"
    )
    assert result_2.marriage_count == 0 if hasattr(result_2, "marriage_count") else True


@pytest.mark.asyncio
async def test_initiator_side_also_loses_bonus_after_breakup(session):
    """The user who initiated the break-up also gets count_marriages=0 immediately."""
    user_a, user_b = 8200, 8201
    await _seed_marriage(session, user_a, user_b)
    rel_repo = RelationshipRepository(session)

    # A initiates break-up
    await rel_repo.break_rel(user_a, user_b)

    assert await rel_repo.count_marriages(user_a) == 0
    assert await rel_repo.count_marriages(user_b) == 0


@pytest.mark.asyncio
async def test_second_marriage_bonus_unaffected_by_unrelated_breakup(session):
    """If A has two marriages, breaking one still leaves count_marriages=1."""
    user_a, user_b, user_c = 8300, 8301, 8302

    # Seed two separate marriages for user_a
    await _seed_marriage(session, user_a, user_b)

    # Second marriage pair (different row, different b-user)
    a2, c2 = min(user_a, user_c), max(user_a, user_c)
    now = dt.datetime.now(dt.timezone.utc)
    rel2 = Relationship(
        user_a_id=a2,
        user_b_id=c2,
        initiator_id=user_a,
        rel_type="married",
        level=1,
        xp=0,
        status="active",
        created_at=now,
        accepted_at=now,
    )
    session.add(rel2)
    await session.flush()

    rel_repo = RelationshipRepository(session)
    assert await rel_repo.count_marriages(user_a) == 2

    # Break only the A–B marriage
    await rel_repo.break_rel(user_a, user_b)

    assert await rel_repo.count_marriages(user_a) == 1
    bonus = MARRIAGE_DAILY_BONUS * await rel_repo.count_marriages(user_a)
    assert bonus == MARRIAGE_DAILY_BONUS


# ---------------------------------------------------------------------------
# Re-marriage scenario: bonus reappears after break-up with previous partner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_marriage_bonus_reappears_after_remarriage(session):
    """Marriage bonus comes back when A re-marries a new partner after a break-up.

    The broken row for A–B must not shadow the fresh active row for A–C.

    Flow:
      1. A and B are married → count_marriages(A) == 1, bonus > 0.
      2. A breaks up with B  → count_marriages(A) == 0, bonus == 0.
      3. A and C get married  → count_marriages(A) == 1, bonus == MARRIAGE_DAILY_BONUS.
    """
    user_a, user_b, user_c = 8400, 8401, 8402

    rel_repo = RelationshipRepository(session)

    # Step 1 — A and B are married
    await _seed_marriage(session, user_a, user_b)
    assert await rel_repo.count_marriages(user_a) == 1

    # Step 2 — break-up; broken row must not be counted
    await rel_repo.break_rel(user_a, user_b)
    assert await rel_repo.count_marriages(user_a) == 0

    # Step 3 — A marries C (completely independent pair, no UniqueConstraint clash)
    await _seed_marriage(session, user_a, user_c)

    marriages_after = await rel_repo.count_marriages(user_a)
    bonus_after = MARRIAGE_DAILY_BONUS * marriages_after

    assert marriages_after == 1, (
        "count_marriages must return 1 after re-marrying a new partner"
    )
    assert bonus_after == MARRIAGE_DAILY_BONUS, (
        "Marriage bonus must equal MARRIAGE_DAILY_BONUS after re-marriage"
    )
    # Broken row with B must not contribute
    assert await rel_repo.count_marriages(user_b) == 0, (
        "Ex-partner B's count must remain 0 after the break-up"
    )


@pytest.mark.asyncio
async def test_remarriage_daily_claim_includes_bonus(session):
    """claim_daily after re-marrying a new partner pays the full marriage bonus.

    Wires RelationshipRepository + WalletRepository together the same way
    the route handler does to confirm the end-to-end coin reward is correct.

    Steps:
      1. A–B married, then broken → marriage_bonus == 0.
      2. A–C married              → marriage_bonus == MARRIAGE_DAILY_BONUS.
      3. Claim daily for A        → earned > base (marriage bonus included).
    """
    user_a, user_b, user_c = 8500, 8501, 8502

    await _seed_wallet(session, user_a)
    await _seed_marriage(session, user_a, user_b)

    rel_repo    = RelationshipRepository(session)
    wallet_repo = WalletRepository(session)

    # Break up with B
    await rel_repo.break_rel(user_a, user_b)
    assert await rel_repo.count_marriages(user_a) == 0

    # Re-marry C
    await _seed_marriage(session, user_a, user_c)
    marriages = await rel_repo.count_marriages(user_a)
    marriage_bonus = MARRIAGE_DAILY_BONUS * marriages

    assert marriage_bonus == MARRIAGE_DAILY_BONUS, (
        "marriage_bonus must be MARRIAGE_DAILY_BONUS after re-marrying"
    )

    result = await wallet_repo.claim_daily(
        user_a,
        streak_days=0,
        premium_multiplier=1.0,
        premium_bonus=marriage_bonus,
    )
    assert result.premium_bonus == MARRIAGE_DAILY_BONUS
    assert result.earned > result.base, (
        "Earned coins must exceed base when re-marriage bonus is applied"
    )
