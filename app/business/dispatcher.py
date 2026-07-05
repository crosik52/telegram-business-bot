"""Bot and Dispatcher setup for the aiogram Business API integration."""

from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.commands import router as commands_router
from app.business.handlers import router as business_router
from app.config import Settings

_bot: Bot | None = None
_dispatcher: Dispatcher | None = None


def get_bot(settings: Settings) -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    return _bot


def get_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
        _dispatcher.include_router(commands_router)
        _dispatcher.include_router(business_router)
    return _dispatcher


async def close_bot() -> None:
    global _bot
    if _bot is not None:
        await _bot.session.close()
        _bot = None
