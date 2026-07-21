"""Tests for AI analysis cache invalidation.

Covers two critical paths:

1. ``invalidate_cache(session, owner_id, chat_id)`` — called when
   ``deleted_business_messages`` fires; must evict the matching
   ``AiAnalysisCache`` row from the DB *and* from the L1 in-memory dict.

2. ``invalidate_cache_for_owner(session, owner_id)`` — called when a
   ``BusinessConnection`` is disabled/revoked; must evict *all*
   ``AiAnalysisCache`` rows for that owner from both layers.

These tests use an in-memory SQLite database so no running Postgres instance
is required.  They exercise the service functions directly (not the aiogram
handlers), which isolates the cache invalidation logic from Telegram transport
concerns.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
from app.models.ai_analysis_cache import AiAnalysisCache
from app.services import ai_analysis_service
from app.services.ai_analysis_service import (
    _CACHE,
    invalidate_cache,
    invalidate_cache_for_owner,
)

DATABASE_URL = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def engine():
    eng = create_async_engine(DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture()
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture(autouse=True)
async def clear_l1_cache():
    """Ensure the module-level L1 dict is clean before and after each test."""
    _CACHE.clear()
    yield
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------

async def _seed_cache_row(
    session: AsyncSession,
    owner_id: int,
    chat_id: int,
    result: dict | None = None,
) -> AiAnalysisCache:
    """Insert one AiAnalysisCache row and commit."""
    if result is None:
        result = {"score": 7}
    row = AiAnalysisCache(
        owner_id=owner_id,
        chat_id=chat_id,
        result_json=json.dumps(result),
    )
    session.add(row)
    await session.commit()
    return row


# ---------------------------------------------------------------------------
# Tests: invalidate_cache (single chat)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalidate_cache_removes_db_row(session_factory):
    """invalidate_cache must delete the matching AiAnalysisCache row from DB."""
    owner_id, chat_id = 1001, 2001

    async with session_factory() as seed_sess:
        await _seed_cache_row(seed_sess, owner_id, chat_id)

    async with session_factory() as sess:
        # Confirm the row exists before invalidation.
        row_before = await sess.scalar(
            select(AiAnalysisCache).where(
                AiAnalysisCache.owner_id == owner_id,
                AiAnalysisCache.chat_id == chat_id,
            )
        )
        assert row_before is not None, "Row must exist before invalidation"

        await invalidate_cache(sess, owner_id, chat_id)
        # invalidate_cache commits internally; no extra commit needed.

        row_after = await sess.scalar(
            select(AiAnalysisCache).where(
                AiAnalysisCache.owner_id == owner_id,
                AiAnalysisCache.chat_id == chat_id,
            )
        )
        assert row_after is None, "DB row must be removed after invalidate_cache"


@pytest.mark.asyncio
async def test_invalidate_cache_removes_l1_entry(session_factory):
    """invalidate_cache must evict the corresponding L1 in-memory entry."""
    owner_id, chat_id = 1002, 2002

    async with session_factory() as seed_sess:
        await _seed_cache_row(seed_sess, owner_id, chat_id)

    # Manually populate L1 so the key is definitely present.
    ai_analysis_service._l1_set(owner_id, chat_id, {"score": 7})
    assert (owner_id, chat_id) in _CACHE, "L1 entry must exist before invalidation"

    async with session_factory() as sess:
        await invalidate_cache(sess, owner_id, chat_id)

    assert (owner_id, chat_id) not in _CACHE, (
        "L1 entry must be removed after invalidate_cache"
    )


@pytest.mark.asyncio
async def test_invalidate_cache_does_not_affect_other_chats(session_factory):
    """invalidate_cache must only evict the targeted (owner, chat) pair."""
    owner_id = 1003
    target_chat_id = 2003
    other_chat_id = 2004

    async with session_factory() as seed_sess:
        await _seed_cache_row(seed_sess, owner_id, target_chat_id)
        await _seed_cache_row(seed_sess, owner_id, other_chat_id)

    # Populate L1 for both chats.
    ai_analysis_service._l1_set(owner_id, target_chat_id, {"score": 5})
    ai_analysis_service._l1_set(owner_id, other_chat_id, {"score": 8})

    async with session_factory() as sess:
        await invalidate_cache(sess, owner_id, target_chat_id)

    # Target evicted.
    assert (owner_id, target_chat_id) not in _CACHE
    # Other chat untouched in L1.
    assert (owner_id, other_chat_id) in _CACHE

    # Other chat row still present in DB.
    async with session_factory() as check_sess:
        remaining = await check_sess.scalar(
            select(AiAnalysisCache).where(
                AiAnalysisCache.owner_id == owner_id,
                AiAnalysisCache.chat_id == other_chat_id,
            )
        )
    assert remaining is not None, "Non-targeted DB row must survive invalidation"


@pytest.mark.asyncio
async def test_invalidate_cache_is_idempotent(session_factory):
    """Calling invalidate_cache twice on a missing row must not raise."""
    owner_id, chat_id = 1004, 2004

    async with session_factory() as sess:
        # First call with no row in DB — should not raise.
        await invalidate_cache(sess, owner_id, chat_id)

    async with session_factory() as sess2:
        # Second call after first already cleared it — still no exception.
        await invalidate_cache(sess2, owner_id, chat_id)


# ---------------------------------------------------------------------------
# Tests: invalidate_cache_for_owner (all chats)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalidate_cache_for_owner_removes_all_db_rows(session_factory):
    """invalidate_cache_for_owner must delete every AiAnalysisCache row for
    the given owner."""
    owner_id = 1010
    chat_ids = [3001, 3002, 3003]

    async with session_factory() as seed_sess:
        for cid in chat_ids:
            await _seed_cache_row(seed_sess, owner_id, cid)

    async with session_factory() as sess:
        await invalidate_cache_for_owner(sess, owner_id)

    async with session_factory() as check_sess:
        rows = (
            await check_sess.execute(
                select(AiAnalysisCache).where(AiAnalysisCache.owner_id == owner_id)
            )
        ).scalars().all()

    assert rows == [], (
        f"All {len(chat_ids)} rows for owner must be removed; "
        f"found {len(rows)} remaining"
    )


@pytest.mark.asyncio
async def test_invalidate_cache_for_owner_removes_all_l1_entries(session_factory):
    """invalidate_cache_for_owner must evict all L1 entries for the owner."""
    owner_id = 1011
    chat_ids = [3010, 3011, 3012]

    async with session_factory() as seed_sess:
        for cid in chat_ids:
            await _seed_cache_row(seed_sess, owner_id, cid)

    # Populate L1 for all chats.
    for cid in chat_ids:
        ai_analysis_service._l1_set(owner_id, cid, {"score": 6})

    assert all((owner_id, cid) in _CACHE for cid in chat_ids), (
        "All L1 entries must exist before invalidation"
    )

    async with session_factory() as sess:
        await invalidate_cache_for_owner(sess, owner_id)

    remaining_l1 = [(owner_id, cid) for cid in chat_ids if (owner_id, cid) in _CACHE]
    assert remaining_l1 == [], (
        f"All L1 entries for owner must be evicted; {remaining_l1} still present"
    )


@pytest.mark.asyncio
async def test_invalidate_cache_for_owner_does_not_affect_other_owners(
    session_factory,
):
    """invalidate_cache_for_owner must only remove rows for the targeted owner."""
    owner_a = 1020
    owner_b = 1021
    shared_chat = 4001

    async with session_factory() as seed_sess:
        await _seed_cache_row(seed_sess, owner_a, shared_chat)
        await _seed_cache_row(seed_sess, owner_b, shared_chat)

    # Populate L1 for both owners.
    ai_analysis_service._l1_set(owner_a, shared_chat, {"score": 5})
    ai_analysis_service._l1_set(owner_b, shared_chat, {"score": 9})

    async with session_factory() as sess:
        await invalidate_cache_for_owner(sess, owner_a)

    # owner_a entries gone from L1.
    assert (owner_a, shared_chat) not in _CACHE
    # owner_b entry untouched in L1.
    assert (owner_b, shared_chat) in _CACHE

    # owner_b DB row must survive.
    async with session_factory() as check_sess:
        row_b = await check_sess.scalar(
            select(AiAnalysisCache).where(
                AiAnalysisCache.owner_id == owner_b,
                AiAnalysisCache.chat_id == shared_chat,
            )
        )
    assert row_b is not None, "Other owner's DB row must survive invalidation"


@pytest.mark.asyncio
async def test_invalidate_cache_for_owner_is_idempotent(session_factory):
    """Calling invalidate_cache_for_owner on an owner with no rows must not raise."""
    owner_id = 9999

    async with session_factory() as sess:
        await invalidate_cache_for_owner(sess, owner_id)

    async with session_factory() as sess2:
        await invalidate_cache_for_owner(sess2, owner_id)
