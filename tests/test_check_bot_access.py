"""Unit tests for channel_subscription_service.check_bot_access.

Tests the four key scenarios the admin channel warning depends on:
  1. Bot is administrator / creator → True
  2. Bot is member / restricted / left / kicked → False
  3. TelegramForbiddenError (no channel access) → False
  4. TelegramBadRequest "user not found" (bot not in channel) → False
  5. Transient unexpected errors → True (fail open, no spurious badge)
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.services.channel_subscription_service import check_bot_access


# ── Minimal fixture for RequiredChannel ──────────────────────────────────────

def _make_channel(username: str = "testchannel") -> MagicMock:
    ch = MagicMock()
    ch.at_username = f"@{username}"
    return ch


def _make_bot(member_status: str | None = None, side_effect=None) -> AsyncMock:
    """Return a mock Bot whose get_chat_member either returns a member with
    the given status or raises *side_effect*."""
    bot = AsyncMock()
    if side_effect is not None:
        bot.get_chat_member.side_effect = side_effect
    else:
        member = MagicMock()
        member.status = member_status
        bot.get_chat_member.return_value = member
    return bot


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_administrator_returns_true():
    """Bot with administrator status → has access."""
    bot = _make_bot("administrator")
    assert await check_bot_access(bot, _make_channel(), bot_id=123) is True


@pytest.mark.asyncio
async def test_creator_returns_true():
    """Bot with creator status → has access."""
    bot = _make_bot("creator")
    assert await check_bot_access(bot, _make_channel(), bot_id=123) is True


@pytest.mark.asyncio
async def test_member_returns_false():
    """Bot is a plain member (no admin rights) → cannot check subscriptions."""
    bot = _make_bot("member")
    assert await check_bot_access(bot, _make_channel(), bot_id=123) is False


@pytest.mark.asyncio
async def test_restricted_returns_false():
    """Bot is restricted → cannot check subscriptions."""
    bot = _make_bot("restricted")
    assert await check_bot_access(bot, _make_channel(), bot_id=123) is False


@pytest.mark.asyncio
async def test_left_returns_false():
    """Bot has left the channel → no access."""
    bot = _make_bot("left")
    assert await check_bot_access(bot, _make_channel(), bot_id=123) is False


@pytest.mark.asyncio
async def test_kicked_returns_false():
    """Bot was kicked → no access."""
    bot = _make_bot("kicked")
    assert await check_bot_access(bot, _make_channel(), bot_id=123) is False


@pytest.mark.asyncio
async def test_forbidden_returns_false():
    """TelegramForbiddenError → bot has no access to the channel at all."""
    exc = TelegramForbiddenError(
        method=MagicMock(), message="Forbidden: bot is not a member of the channel chat"
    )
    bot = _make_bot(side_effect=exc)
    assert await check_bot_access(bot, _make_channel(), bot_id=123) is False


@pytest.mark.asyncio
async def test_bad_request_user_not_found_returns_false():
    """TelegramBadRequest 'user not found' → bot not in channel → no access."""
    exc = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: user not found"
    )
    bot = _make_bot(side_effect=exc)
    assert await check_bot_access(bot, _make_channel(), bot_id=123) is False


@pytest.mark.asyncio
async def test_bad_request_chat_not_found_returns_false():
    """TelegramBadRequest 'chat not found' → channel misconfigured → no access."""
    exc = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: chat not found"
    )
    bot = _make_bot(side_effect=exc)
    assert await check_bot_access(bot, _make_channel(), bot_id=123) is False


@pytest.mark.asyncio
async def test_transient_error_returns_true():
    """Unexpected/transient error → fail open to avoid spurious warning badges."""
    bot = _make_bot(side_effect=OSError("network timeout"))
    assert await check_bot_access(bot, _make_channel(), bot_id=123) is True


@pytest.mark.asyncio
async def test_uses_provided_bot_id_without_get_me():
    """When bot_id is supplied, get_me() should not be called."""
    bot = _make_bot("administrator")
    await check_bot_access(bot, _make_channel(), bot_id=999)
    bot.get_me.assert_not_called()
    bot.get_chat_member.assert_awaited_once_with("@testchannel", 999)
