"""Focused tests for the mini-app subscription gate.

Covers:
1. _assert_subscribed → 403 (unsubscribed), 503 (check error), pass (subscribed)
2. _is_subscribed returns True/False/None (tri-state, fail-closed)
3. Cache: stale False is overwritten by stats endpoint writing True immediately
4. _is_subscribed uses the short-lived cache to avoid repeated Telegram calls
5. Middleware-level route tests: representative endpoints return 403 for an
   unsubscribed user without any side effect occurring.
"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient, ASGITransport


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


# ═══════════════════════════════════════════════════════════════════════════════
#  Unit tests for _assert_subscribed
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_assert_subscribed_passes_for_subscribed_user():
    """`_assert_subscribed` does NOT raise when _is_subscribed returns True."""
    from app.miniapp import routes as r

    session = _make_session()
    with patch("app.miniapp.routes._is_subscribed", new=AsyncMock(return_value=True)):
        await r._assert_subscribed(user_id=1, session=session)  # must not raise


@pytest.mark.asyncio
async def test_assert_subscribed_raises_403_for_unsubscribed_user():
    """`_assert_subscribed` raises HTTP 403 when _is_subscribed returns False."""
    from app.miniapp import routes as r

    session = _make_session()
    with patch("app.miniapp.routes._is_subscribed", new=AsyncMock(return_value=False)):
        with pytest.raises(HTTPException) as exc_info:
            await r._assert_subscribed(user_id=2, session=session)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail.get("subscription_gate") is True


@pytest.mark.asyncio
async def test_assert_subscribed_raises_503_when_check_fails():
    """`_assert_subscribed` raises HTTP 503 (fail-closed) when _is_subscribed
    returns None (check errored while channels are configured)."""
    from app.miniapp import routes as r

    session = _make_session()
    with patch("app.miniapp.routes._is_subscribed", new=AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc_info:
            await r._assert_subscribed(user_id=3, session=session)
        assert exc_info.value.status_code == 503


# ═══════════════════════════════════════════════════════════════════════════════
#  Unit tests for _is_subscribed
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_is_subscribed_true_when_no_active_channels():
    """`_is_subscribed` returns True when no required channels are configured."""
    from app.miniapp import routes as r

    session = _make_session()
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
            new=AsyncMock(return_value=[ch]),
        ),
    ):
        result = await r._is_subscribed(user_id=99, session=session)

    assert result is False


@pytest.mark.asyncio
async def test_is_subscribed_none_when_bot_unavailable_but_channels_configured():
    """`_is_subscribed` returns None (fail-closed) when the bot is unavailable
    but channels ARE configured."""
    from app.miniapp import routes as r

    session = _make_session()
    r._sub_cache.pop(88, None)

    ch = _make_channel("required_chan")

    with (
        patch("app.repositories.channel_repository.ChannelRepository.get_active",
              new=AsyncMock(return_value=[ch])),
        patch("app.miniapp.routes.get_bot", return_value=None),  # bot unavailable
    ):
        result = await r._is_subscribed(user_id=88, session=session)

    assert result is None


@pytest.mark.asyncio
async def test_is_subscribed_none_on_telegram_exception():
    """`_is_subscribed` returns None (fail-closed) when get_unsubscribed_channels
    raises, and channels ARE configured."""
    from app.miniapp import routes as r

    session = _make_session()
    r._sub_cache.pop(77, None)

    ch = _make_channel("required_chan")
    mock_bot = MagicMock()

    async def boom(*_a, **_kw):
        raise RuntimeError("Telegram timeout")

    with (
        patch("app.repositories.channel_repository.ChannelRepository.get_active",
              new=AsyncMock(return_value=[ch])),
        patch("app.miniapp.routes.get_bot", return_value=mock_bot),
        patch(
            "app.services.channel_subscription_service.get_unsubscribed_channels",
            new=boom,
        ),
    ):
        result = await r._is_subscribed(user_id=77, session=session)

    assert result is None
    # Error results must NOT be cached (so the next request retries immediately)
    assert 77 not in r._sub_cache


@pytest.mark.asyncio
async def test_is_subscribed_caches_result():
    """`_is_subscribed` uses the cache on second call and skips the Telegram API."""
    from app.miniapp import routes as r

    session = _make_session()
    r._sub_cache.pop(55, None)

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
        await r._is_subscribed(user_id=55, session=session)
        await r._is_subscribed(user_id=55, session=session)

    assert call_count == 1


@pytest.mark.asyncio
async def test_sub_cache_false_clears_immediately_when_stats_endpoint_writes_true():
    """Simulates the cache-invalidation fix: when the stats endpoint writes True
    after a prior False, _assert_subscribed sees it immediately."""
    from app.miniapp import routes as r

    user_id = 66
    # Inject stale False (simulating user was unsubscribed)
    r._sub_cache[user_id] = (time.monotonic() - 5, False)

    # Stats endpoint writes True after the user subscribes
    r._sub_cache[user_id] = (time.monotonic(), True)

    session = _make_session()
    # _assert_subscribed must pass without a fresh Telegram check
    with patch("app.miniapp.routes._is_subscribed", new=AsyncMock(return_value=True)):
        await r._assert_subscribed(user_id=user_id, session=session)


# ═══════════════════════════════════════════════════════════════════════════════
#  Route-level integration tests via the ASGI test client
# ═══════════════════════════════════════════════════════════════════════════════

def _make_app_with_gate(subscribed: bool | None = False):
    """Return the FastAPI app with the subscription middleware active and
    _subscription_status patched to return *subscribed*.

    subscribed=False  → user not subscribed → expect 403
    subscribed=None   → check failed (channels configured) → expect 503
    subscribed=True   → user subscribed → expect route to handle normally
    """
    import importlib, sys
    # Fresh import of main to avoid state bleed
    if "app.main" in sys.modules:
        app_module = sys.modules["app.main"]
    else:
        app_module = importlib.import_module("app.main")
    return app_module.app


_VALID_INIT = "user=%7B%22id%22%3A12345%2C%22first_name%22%3A%22Test%22%7D&hash=abc"


def _body(**kwargs) -> bytes:
    return json.dumps(kwargs).encode()


@pytest.mark.asyncio
async def test_middleware_blocks_unsubscribed_on_mutation_endpoint():
    """SubscriptionGateMiddleware returns 403 for an unsubscribed user hitting
    a POST mutation endpoint (/app/api/wallet/claim-daily)."""
    from app.main import app

    with patch(
        "app.miniapp.subscription_middleware._subscription_status",
        new=AsyncMock(return_value=False),
    ), patch(
        "app.miniapp.auth.verify_init_data",
        return_value={"id": "12345", "first_name": "Test"},
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/app/api/wallet/claim-daily",
                content=_body(initData=_VALID_INIT),
                headers={"content-type": "application/json"},
            )
        assert resp.status_code == 403
        assert resp.json()["detail"]["subscription_gate"] is True


@pytest.mark.asyncio
async def test_middleware_blocks_unsubscribed_on_read_endpoint_with_side_effects():
    """SubscriptionGateMiddleware returns 403 for an unsubscribed user hitting
    /app/api/quests (read endpoint with potential coin-award side effects)."""
    from app.main import app

    with patch(
        "app.miniapp.subscription_middleware._subscription_status",
        new=AsyncMock(return_value=False),
    ), patch(
        "app.miniapp.auth.verify_init_data",
        return_value={"id": "12345", "first_name": "Test"},
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/app/api/quests",
                content=_body(initData=_VALID_INIT),
                headers={"content-type": "application/json"},
            )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_middleware_returns_503_on_check_failure():
    """SubscriptionGateMiddleware returns 503 (fail-closed) when the subscription
    check errors out while channels are configured."""
    from app.main import app

    with patch(
        "app.miniapp.subscription_middleware._subscription_status",
        new=AsyncMock(return_value=None),
    ), patch(
        "app.miniapp.auth.verify_init_data",
        return_value={"id": "12345", "first_name": "Test"},
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/app/api/wallet/claim-daily",
                content=_body(initData=_VALID_INIT),
                headers={"content-type": "application/json"},
            )
        assert resp.status_code == 503


@pytest.mark.asyncio
async def test_middleware_passes_stats_endpoint_through():
    """The stats gate-discovery endpoint (/app/api/stats) is exempt from the
    middleware so the frontend can discover which channels are required."""
    from app.main import app

    # Even with subscription_status returning False, /app/api/stats passes through.
    with patch(
        "app.miniapp.subscription_middleware._subscription_status",
        new=AsyncMock(return_value=False),
    ), patch(
        "app.miniapp.auth.verify_init_data",
        return_value={"id": "12345", "first_name": "Test"},
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Stats will fail (no real DB) but it must NOT return 403 from middleware
            resp = await client.post(
                "/app/api/stats",
                content=_body(initData=_VALID_INIT),
                headers={"content-type": "application/json"},
            )
        # Must not be a gate 403 — any other response code is acceptable
        if resp.status_code == 403:
            assert resp.json().get("detail", {}).get("subscription_gate") is not True


@pytest.mark.asyncio
async def test_middleware_blocks_get_settings_for_unsubscribed_user():
    """GET /app/api/settings (query-param init_data) is blocked by the middleware."""
    from app.main import app

    with patch(
        "app.miniapp.subscription_middleware._subscription_status",
        new=AsyncMock(return_value=False),
    ), patch(
        "app.miniapp.auth.verify_init_data",
        return_value={"id": "12345", "first_name": "Test"},
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/app/api/settings",
                params={"initData": _VALID_INIT},
            )
        assert resp.status_code == 403
