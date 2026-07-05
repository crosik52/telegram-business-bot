"""Regular Telegram bot commands (not part of the Business API).

These handlers respond to plain private-chat messages sent directly to the
bot itself (e.g. `/start`). They are separate from `app/business/handlers.py`,
which only reacts to Business API updates (business_message, etc.) coming
from the owner's connected personal account.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger(__name__)
router = Router(name="commands")

_GREETING = (
    "\U0001f44b Привет! Я бот для Telegram для бизнеса.\n\n"
    "Я сохраняю сообщения из подключённого Business-аккаунта, включая их правки и удаления, "
    "и пришлю тебе уведомление, если собеседник отредактирует или удалит своё сообщение.\n\n"
    "Открой свою статистику общения кнопкой ниже \u2014 там топ собеседников и другие цифры.\n\n"
    "Чтобы бот начал получать сообщения, подключи его в Telegram: "
    "Настройки \u2192 Telegram для бизнеса \u2192 Чат-боты."
)


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    """Greet the user and offer a button to open their personal stats mini app."""

    settings = get_settings()

    reply_markup: InlineKeyboardMarkup | None = None
    if settings.webhook_base_url:
        base_url = settings.webhook_base_url.rstrip("/")
        keyboard = [
            [
                InlineKeyboardButton(
                    text="\U0001f4ca Моя статистика",
                    web_app=WebAppInfo(url=base_url + "/app"),
                )
            ]
        ]

        username = (message.from_user.username or "").lstrip("@").lower() if message.from_user else ""
        admin_username = settings.miniapp_admin_username.lstrip("@").lower()
        if admin_username and username == admin_username:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="\U0001f6e0 Админ-панель",
                        web_app=WebAppInfo(url=base_url + "/app/admin"),
                    )
                ]
            )

        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await message.answer(_GREETING, reply_markup=reply_markup)
    logger.info("Sent /start greeting to chat_id=%s", message.chat.id)
