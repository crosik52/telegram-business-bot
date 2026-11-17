"""Bounded, fail-closed Telegram channel membership and Premium boost checks."""
from __future__ import annotations

import asyncio
import datetime as dt
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.logging_config import get_logger
from app.models.required_channel import RequiredChannel

logger = get_logger(__name__)
MAX_REQUIRED_CHANNELS = 10
CHECK_TIMEOUT = 8.0
_CHECK_SLOTS = asyncio.Semaphore(4)
_ADMIN_STATUSES = {"administrator", "creator"}
_SUBSCRIBED_STATUSES = _ADMIN_STATUSES | {"member"}
_NOT_MEMBER_PHRASES = ("user not found", "participant_id_invalid", "user_not_participant")


class ChannelCheckUnavailable(RuntimeError):
    """The configured requirement cannot currently be verified."""


@dataclass(frozen=True)
class ChannelRequirement:
    channel: RequiredChannel
    needs_subscription: bool
    needs_boost: bool

    @property
    def display_title(self) -> str:
        return self.channel.display_title

    @property
    def channel_username(self) -> str:
        return self.channel.channel_username

    @property
    def join_url(self) -> str:
        return self.channel.join_url

    @property
    def boost_url(self) -> str:
        return self.channel.boost_url

    def as_dict(self) -> dict:
        return {"title": self.display_title, "username": self.channel_username,
                "url": self.join_url, "boost_url": self.boost_url,
                "needs_subscription": self.needs_subscription, "needs_boost": self.needs_boost}


def normalize_channel(value: str) -> str:
    value = value.strip()
    if value.startswith(("https://", "http://")):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.netloc.lower() != "t.me" or parsed.query or parsed.fragment:
            raise ValueError("Укажите @username, https://t.me/username или ID -100…")
        value = parsed.path.strip("/")
    value = value.removeprefix("@")
    if re.fullmatch(r"-100[1-9][0-9]{5,15}", value):
        return value
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", value):
        raise ValueError("Укажите корректный username канала или ID -100…")
    return value.lower()


def normalize_join_url(value: str | None, target: str) -> str | None:
    value = (value or "").strip()
    if not value:
        if target.startswith("-"):
            raise ValueError("Для закрытого канала укажите ссылку приглашения https://t.me/+…")
        return None
    parsed = urlsplit(value)
    if (len(value) > 512 or parsed.scheme != "https" or parsed.netloc.lower() != "t.me"
            or parsed.query or parsed.fragment
            or not re.fullmatch(r"/(?:\+[A-Za-z0-9_-]+|joinchat/[A-Za-z0-9_-]+|[A-Za-z][A-Za-z0-9_]{3,31})", parsed.path)):
        raise ValueError("Ссылка должна вести на канал или приглашение https://t.me/…")
    return value


async def _check_one(bot: Bot, user_id: int, ch: RequiredChannel, *, is_premium: bool = False) -> ChannelRequirement | None:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT), _CHECK_SLOTS:
            try:
                member = await bot.get_chat_member(ch.at_username, user_id)
            except TelegramBadRequest as exc:
                if any(phrase in str(exc).lower() for phrase in _NOT_MEMBER_PHRASES):
                    return ChannelRequirement(ch, True, bool(ch.require_premium_boost and is_premium))
                raise
            subscribed = (member.status in _SUBSCRIBED_STATUSES
                          or (member.status == "restricted" and getattr(member, "is_member", False) is True))
            # Telegram's current User object also prevents replaying old non-Premium initData.
            premium = getattr(member.user, "is_premium", False) is True
            needs_boost = False
            if ch.require_premium_boost and premium:
                boosts = await bot.get_user_chat_boosts(chat_id=ch.at_username, user_id=user_id)
                now = dt.datetime.now(dt.UTC)
                active = {boost.boost_id for boost in boosts.boosts
                          if boost.expiration_date > now
                          and getattr(getattr(boost.source, "user", None), "id", None) == user_id}
                needs_boost = len(active) < 1
            if not subscribed or needs_boost:
                return ChannelRequirement(ch, not subscribed, needs_boost)
            return None
    except Exception as exc:
        # Do not log exception text/traceback: Telegram transport errors may contain token URLs.
        logger.warning("channel_gate check unavailable channel_id=%s error_type=%s", ch.id, type(exc).__name__)
        raise ChannelCheckUnavailable("Channel check unavailable") from None


async def check_bot_access(bot: Bot, ch: RequiredChannel, *, bot_id: int | None = None) -> bool | None:
    """True=admin, False=definite rejection, None=temporarily unverifiable."""
    try:
        async with asyncio.timeout(CHECK_TIMEOUT), _CHECK_SLOTS:
            probe_id = bot_id or bot.id
            member = await bot.get_chat_member(ch.at_username, probe_id)
            return member.status in _ADMIN_STATUSES
    except (TelegramForbiddenError, TelegramBadRequest):
        return False
    except Exception as exc:
        logger.warning("channel_gate access unavailable channel_id=%s error_type=%s", ch.id, type(exc).__name__)
        return None


# One bounded process-local cache also protects inline queries (one event per keystroke).
# Keys include channel configuration and the event loop, so admin changes and test
# loops cannot reuse an old in-flight check. The current Telegram User is authoritative.
_GATE_CACHE: dict[tuple, tuple[float, list[ChannelRequirement]]] = {}
_PENDING_CHECKS: dict[tuple, asyncio.Task] = {}
_GATE_CACHE_MAX = 2048
_MAX_PENDING_CHECKS = 64


def clear_gate_cache() -> None:
    _GATE_CACHE.clear()


async def get_unsubscribed_channels(bot: Bot, user_id: int, channels: list[RequiredChannel], *, is_premium: bool = False, force: bool = False) -> list[ChannelRequirement]:
    """Coalesce repeated checks; API errors never grant access or enter the cache."""
    if len(channels) > MAX_REQUIRED_CHANNELS:
        raise ChannelCheckUnavailable("Too many required channels")
    if not channels:
        return []
    key = (id(asyncio.get_running_loop()), bot.id, user_id, tuple(
        (ch.id, ch.channel_username, ch.require_premium_boost, ch.channel_join_url)
        for ch in channels))
    now = time.monotonic()
    cached = _GATE_CACHE.get(key)
    if cached and now < cached[0] and not force:
        return cached[1]
    # Explicit retry still has a one-second minimum interval to bound button spam.
    if cached and force and now < cached[0] - (7.0 if not cached[1] else 0.0):
        return cached[1]
    pending = _PENDING_CHECKS.get(key)
    if pending is not None:
        return await asyncio.shield(pending)
    if len(_PENDING_CHECKS) >= _MAX_PENDING_CHECKS:
        raise ChannelCheckUnavailable("Channel checker busy")

    async def run_checks() -> list[ChannelRequirement]:
        # The task owns cleanup/cache updates. Cancelling one HTTP request must
        # not cancel other callers waiting for this same bounded check.
        try:
            results = await asyncio.gather(*(_check_one(bot, user_id, ch, is_premium=is_premium) for ch in channels))
            missing = [result for result in results if result is not None]
            if len(_GATE_CACHE) >= _GATE_CACHE_MAX:
                _GATE_CACHE.pop(next(iter(_GATE_CACHE)))
            _GATE_CACHE[key] = (time.monotonic() + (1.0 if missing else 8.0), missing)
            return missing
        finally:
            _PENDING_CHECKS.pop(key, None)

    pending = asyncio.create_task(run_checks())
    _PENDING_CHECKS[key] = pending
    # Retrieve failures even if every original waiter has disconnected.
    pending.add_done_callback(lambda task: None if task.cancelled() else task.exception())
    return await asyncio.shield(pending)
