"""Permission notification helpers for Business-API features.

When the owner tries to use a feature that requires a Telegram Business
permission they haven't granted, the bot sends them a DM explaining exactly
what to enable and where.
"""

from __future__ import annotations

from aiogram import Bot

from app.bot import emoji as E
from app.logging_config import get_logger

logger = get_logger(__name__)

_SETTINGS_PATH = (
    "Настройки → Telegram для бизнеса → Чат-боты → "
    "[выбери бота] → Разрешения"
)

# Maps Telegram BusinessBotRights field names → human-readable explanation.
_MESSAGES: dict[str, str] = {
    "can_reply": (
        f"{E.WARNING} <b>Нет разрешения «Ответы на сообщения»</b>\n\n"
        "Функция <b>{feature}</b> отправляет сообщения в чат от твоего имени "
        "и требует соответствующего разрешения.\n\n"
        "Как включить:\n"
        f"<b>{_SETTINGS_PATH}</b> → {E.CHECK} <b>Ответы на сообщения</b>\n\n"
        "После включения функция заработает сразу."
    ),
}


async def notify_missing(
    bot: Bot,
    owner_id: int,
    permission: str,
    feature: str,
) -> None:
    """DM the owner about a missing Business permission.

    Args:
        bot:        Aiogram Bot instance.
        owner_id:   Telegram user_id of the business account owner.
        permission: The BusinessBotRights field name that is missing
                    (e.g. ``"can_reply"``).
        feature:    Short human-readable name of the feature that needs it
                    (e.g. ``"!mp3"`` or ``"автоскачивание видео"``).
    """
    template = _MESSAGES.get(
        permission,
        f"{E.WARNING} Для функции <b>{{feature}}</b> нужно разрешение "
        f"<code>{permission}</code>.\n\n"
        f"Открой: <b>{_SETTINGS_PATH}</b>",
    )
    text = template.format(feature=feature)
    try:
        await bot.send_message(chat_id=owner_id, text=text, parse_mode="HTML")
    except Exception as exc:
        logger.debug(
            "permissions: could not notify owner %s about %s: %s",
            owner_id, permission, exc,
        )
