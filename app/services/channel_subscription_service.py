"""Check whether a Telegram user is subscribed to required channels."""
from __future__ import annotations

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


async def get_unsubscribed_channels(
    bot: Bot,
    user_id: int,
    channels: list[RequiredChannel],
) -> list[RequiredChannel]:
    """Return the subset of *channels* the user is NOT subscribed to.

    Error-handling policy
    ─────────────────────
    • User is explicitly left/kicked → block (treat as not subscribed).
    • Bot lacks access to the channel (Forbidden / chat not found) → fail OPEN:
      log an error so the admin can fix it, but do NOT block the user.
      Blocking everyone because of a misconfigured channel is worse than
      letting someone through we can't verify.
    • Any other unexpected error → fail open + log, same reasoning.
    """
    unsubscribed: list[RequiredChannel] = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch.at_username, user_id)
            if member.status in _NOT_MEMBER_STATUSES:
                unsubscribed.append(ch)
            elif member.status not in _SUBSCRIBED_STATUSES:
                # Unknown status — treat as not subscribed to be safe
                logger.warning(
                    "channel_gate: unknown member status %r for user %s in %s",
                    member.status, user_id, ch.at_username,
                )
                unsubscribed.append(ch)
            # else: subscribed — do nothing

        except TelegramForbiddenError as exc:
            # Bot is not in the channel or was kicked from it.
            # This is a misconfiguration — fail OPEN so real subscribers
            # aren't blocked. Admin must add the bot to the channel.
            logger.error(
                "channel_gate: bot has no access to %s — "
                "add the bot as admin to enable subscription checks. "
                "Letting user %s through. Error: %s",
                ch.at_username, user_id, exc,
            )

        except TelegramBadRequest as exc:
            exc_msg = str(exc).lower()
            if any(phrase in exc_msg for phrase in _USER_NOT_MEMBER_PHRASES):
                # Telegram confirmed the user is not a member
                unsubscribed.append(ch)
            elif "chat not found" in exc_msg or "invalid" in exc_msg:
                # Channel doesn't exist or username is wrong — misconfiguration,
                # fail open so users aren't blocked by a bad channel record.
                logger.error(
                    "channel_gate: channel %s not found or invalid — "
                    "check that the username is correct. "
                    "Letting user %s through. Error: %s",
                    ch.at_username, user_id, exc,
                )
            else:
                # Unknown bad request — fail open, log for investigation
                logger.error(
                    "channel_gate: unexpected TelegramBadRequest for %s / user %s: %s",
                    ch.at_username, user_id, exc,
                )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "channel_gate: unexpected error for %s / user %s — "
                "letting user through: %s",
                ch.at_username, user_id, exc,
            )

    return unsubscribed
