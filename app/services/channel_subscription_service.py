"""Check whether a Telegram user is subscribed to required channels."""
from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.logging_config import get_logger
from app.models.required_channel import RequiredChannel

logger = get_logger(__name__)

_SUBSCRIBED_STATUSES = {"creator", "administrator", "member", "restricted"}


async def get_unsubscribed_channels(
    bot: Bot,
    user_id: int,
    channels: list[RequiredChannel],
) -> list[RequiredChannel]:
    """Return the subset of *channels* the user is NOT subscribed to.

    Errors (bot not in channel, channel not found, etc.) are treated as
    "not subscribed" so the gate does not silently fail open.
    """
    unsubscribed: list[RequiredChannel] = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch.channel_username, user_id)
            if member.status not in _SUBSCRIBED_STATUSES:
                unsubscribed.append(ch)
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            logger.warning(
                "channel_gate: cannot check %s for user %s: %s",
                ch.channel_username, user_id, exc,
            )
            unsubscribed.append(ch)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "channel_gate: unexpected error for %s / user %s: %s",
                ch.channel_username, user_id, exc,
            )
            unsubscribed.append(ch)
    return unsubscribed
