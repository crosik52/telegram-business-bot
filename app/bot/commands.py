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
    "\U0001f44b Привет! Я бот для логирования сообщений Telegram для бизнеса.\n\n"
    "Я сохраняю входящие и исходящие сообщения из подключённого Business-аккаунта, "
    "включая их правки и удаления, в защищённой панели администратора.\n\n"
    "Чтобы бот начал получать сообщения, подключите его в Telegram: "
    "Настройки \u2192 Telegram для бизнеса \u2192 Чат-боты."
)


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    """Greet the user and, if possible, offer a button to open the admin dashboard."""

    settings = get_settings()

    reply_markup: InlineKeyboardMarkup | None = None
    if settings.webhook_base_url:
        dashboard_url = settings.webhook_base_url.rstrip("/") + "/"
        reply_markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="\U0001f4ca Открыть панель администратора",
                        web_app=WebAppInfo(url=dashboard_url),
                    )
                ]
            ]
        )

    await message.answer(_GREETING, reply_markup=reply_markup)
    logger.info("Sent /start greeting to chat_id=%s", message.chat.id)
