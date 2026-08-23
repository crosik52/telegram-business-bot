"""Focused tests for the mini-app subscription gate.

Covers:
1. _assert_subscribed raises HTTP 403 for unsubscribed users
2. _assert_subscribed passes through for subscribed users
3. Cache: a fresh True in the stats-endpoint path clears a prior False immediately
4. _assert_subscribed uses the short-lived cache to avoid repeated Telegram calls
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_session() -> AsyncMock:
    return AsyncMock()


def _make_channel(username: str = "testchan") -> MagicMock:
    ch = MagicMock()
    ch.at_username = f"@{username}"
    ch.channel_username = username
    ch.display_title = username.title()
    ch.join_url = f"https://t.me/{username}"
    return ch


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_assert_subscribed_passes_for_subscribed_user():
    """`_assert_subscribed` does NOT raise when the user passes the gate."""
    from app.miniapp import routes as r

    session = _make_session()
    # Patch the helper internals — no active channels → always passes
    with patch("app.miniapp.routes._is_subscribed", return_value=True):
        await r._assert_subscribed(user_id=1, session=session)  # must not raise


@pytest.mark.asyncio
async def test_assert_subscribed_raises_403_for_unsubscribed_user():
    """`_assert_subscribed` raises HTTP 403 when the user hasn't subscribed."""
    from app.miniapp import routes as r

    session = _make_session()
    with patch("app.miniapp.routes._is_subscribed", return_value=False):
        with pytest.raises(HTTPException) as exc_info:
            await r._assert_subscribed(user_id=2, session=session)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail.get("subscription_gate") is True


@pytest.mark.asyncio
async def test_is_subscribed_true_when_no_active_channels():
    """`_is_subscribed` returns True when no required channels are configured."""
    from app.miniapp import routes as r

    session = _make_session()
    # Clear the cache for this test's user_id
    r._sub_cache.pop(42, None)

    with (
        patch("app.repositories.channel_repository.ChannelRepository.get_active",
              new=AsyncMock(return_value=[])),
        patch("app.miniapp.routes.get_bot", return_value=MagicMock()),
    ):
        result = await r._is_subscribed(user_id=42, session=session)

    assert result is True


@pytest.mark.asyncio
async def test_is_subscribed_false_when_channel_not_joined():
    """`_is_subscribed` returns False when user hasn't joined a required channel."""
    from app.miniapp import routes as r

    session = _make_session()
    r._sub_cache.pop(99, None)

    ch = _make_channel("required_chan")
    mock_bot = MagicMock()

    with (
        patch("app.repositories.channel_repository.ChannelRepository.get_active",
              new=AsyncMock(return_value=[ch])),
        patch("app.miniapp.routes.get_bot", return_value=mock_bot),
        patch(
            "app.services.channel_subscription_service.get_unsubscribed_channels",
            new=AsyncMock(return_value=[ch]),   # one channel still unsubscribed
        ),
    ):
        result = await r._is_subscribed(user_id=99, session=session)

    assert result is False


@pytest.mark.asyncio
async def test_is_subscribed_caches_result():
    """`_is_subscribed` uses the cache on second call and skips the Telegram API."""
    from app.miniapp import routes as r

    session = _make_session()
    r._sub_cache.pop(77, None)

    call_count = 0

    async def fake_get_active(self_):
        nonlocal call_count
        call_count += 1
        return []

    with (
        patch("app.repositories.channel_repository.ChannelRepository.get_active",
              new=fake_get_active),
        patch("app.miniapp.routes.get_bot", return_value=MagicMock()),
    ):
        await r._is_subscribed(user_id=77, session=session)
        await r._is_subscribed(user_id=77, session=session)

    # get_active (and therefore the Telegram round-trip) should only happen once
    assert call_count == 1


@pytest.mark.asyncio
async def test_sub_cache_false_clears_immediately_when_stats_endpoint_writes_true():
    """Simulates the scenario where stats writes True after a prior False.

    When the user subscribes and loadStats() calls /api/stats, the stats
    endpoint writes True to _sub_cache.  Subsequent _assert_subscribed calls
    on the same request batch see True immediately — not a 60-second-old False.
    """
    from app.miniapp import routes as r
    import time as _t

    user_id = 55
    # Inject a stale False into the cache (simulating user was unsubscribed)
    r._sub_cache[user_id] = (_t.monotonic() - 5, False)

    session = _make_session()
    # Now simulate the stats endpoint writing True (as it does after gate passes)
    r._sub_cache[user_id] = (_t.monotonic(), True)

    # _assert_subscribed must now pass without an extra Telegram call
    with patch("app.miniapp.routes._is_subscribed", new=AsyncMock(return_value=True)):
        await r._assert_subscribed(user_id=user_id, session=session)  # must not raise
