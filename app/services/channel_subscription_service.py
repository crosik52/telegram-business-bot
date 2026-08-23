"""Check whether a Telegram user is subscribed to required channels."""
from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.logging_config import get_logger
from app.models.required_channel import RequiredChannel

logger = get_logger(__name__)

_SUBSCRIBED_STATUSES = {"creator", "administrator", "member", "restricted"}

# Statuses that explicitly mean "not a member" — block the user.
_NOT_MEMBER_STATUSES = {"left", "kicked"}

# Phrases in TelegramBadRequest that indicate the *user* is not in the channel
# (as opposed to the bot not having access to the channel).
_USER_NOT_MEMBER_PHRASES = (
    "user not found",
    "participant_id_invalid",
    "user_not_participant",
)

# Telegram can take a few seconds to propagate a new subscription.
# We retry this many times before concluding the user isn't subscribed.
_RETRY_ATTEMPTS = 3
_RETRY_DELAY_S  = 2.0


async def _check_one(bot: Bot, user_id: int, ch: RequiredChannel) -> bool:
    """Return True if *user_id* is subscribed to *ch*, False if not.

    Retries up to _RETRY_ATTEMPTS times to handle Telegram's propagation delay
    (user subscribes → taps "I subscribed" before Telegram's API catches up).
    """
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            member = await bot.get_chat_member(ch.at_username, user_id)

            if member.status in _SUBSCRIBED_STATUSES:
                return True

            if member.status in _NOT_MEMBER_STATUSES:
                if attempt < _RETRY_ATTEMPTS - 1:
                    # Maybe Telegram hasn't propagated the subscription yet — retry
                    await asyncio.sleep(_RETRY_DELAY_S)
                    continue
                return False

            # Unknown status
            logger.warning(
                "channel_gate: unknown member status %r for user %s in %s",
                member.status, user_id, ch.at_username,
            )
            return False

        except TelegramForbiddenError as exc:
            # Bot lacks access to the channel — misconfiguration, fail open.
            logger.error(
                "channel_gate: bot has no access to %s — "
                "add the bot as admin to enable subscription checks. "
                "Letting user %s through. Error: %s",
                ch.at_username, user_id, exc,
            )
            return True  # fail open

        except TelegramBadRequest as exc:
            exc_msg = str(exc).lower()
            if any(phrase in exc_msg for phrase in _USER_NOT_MEMBER_PHRASES):
                if attempt < _RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(_RETRY_DELAY_S)
                    continue
                return False
            if "chat not found" in exc_msg or "invalid" in exc_msg:
                logger.error(
                    "channel_gate: channel %s not found or invalid — "
                    "check the username. Letting user %s through. Error: %s",
                    ch.at_username, user_id, exc,
                )
                return True  # fail open — misconfiguration
            logger.error(
                "channel_gate: unexpected TelegramBadRequest for %s / user %s: %s",
                ch.at_username, user_id, exc,
            )
            return True  # fail open

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "channel_gate: unexpected error for %s / user %s — "
                "letting user through: %s",
                ch.at_username, user_id, exc,
            )
            return True  # fail open

    return False  # exhausted retries


async def check_bot_access(bot: Bot, ch: RequiredChannel, *, bot_id: int | None = None) -> bool:
    """Return True if the bot can call getChatMember on *ch*.

    Uses the bot's own Telegram user ID as a test subject.  If the bot is an
    admin in the channel the call succeeds (or raises a user-not-found
    TelegramBadRequest, which still proves the bot has the required rights).
    If the bot has no admin rights the call raises TelegramForbiddenError —
    the same error that makes the subscription gate silently fail open.

    Pass *bot_id* to avoid an extra get_me() round-trip when checking multiple
    channels from the same context.
    """
    try:
        probe_id = bot_id or (await bot.get_me()).id
        await bot.get_chat_member(ch.at_username, probe_id)
        return True
    except TelegramForbiddenError:
        return False
    except TelegramBadRequest as exc:
        exc_msg = str(exc).lower()
        if "chat not found" in exc_msg or "invalid" in exc_msg:
            return False
        # Other TelegramBadRequest (e.g. "user not found") means the bot IS an
        # admin — it just isn't itself a member of the channel.
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "check_bot_access: unexpected error for %s: %s", ch.at_username, exc
        )
        return True  # fail open — don't show spurious warnings on transient errors


async def get_unsubscribed_channels(
    bot: Bot,
    user_id: int,
    channels: list[RequiredChannel],
) -> list[RequiredChannel]:
    """Return the subset of *channels* the user is NOT subscribed to."""
    results = await asyncio.gather(
        *(_check_one(bot, user_id, ch) for ch in channels)
    )
    return [ch for ch, ok in zip(channels, results) if not ok]
