"""Gate interactive bot features, without touching business ingestion or payments."""
from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, ChosenInlineResult, InlineQuery, Message

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger(__name__)


class BotFeatureGateMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user is None:
            return await handler(event, data)
        # Admin must be able to repair a broken channel requirement.
        if user.id == get_settings().miniapp_admin_id:
            return await handler(event, data)
        if isinstance(event, Message):
            command = (event.text or "").split(maxsplit=1)[0].split("@")[0].lower() if event.text else ""
            if event.successful_payment or command in {"/start", "/help"}:
                return await handler(event, data)
        elif isinstance(event, CallbackQuery):
            if event.data == "check_sub" or (event.data or "").startswith("help_"):
                return await handler(event, data)
            # Closing/cancelling work remains available while access is suspended.
            if event.data in {"mp3_noop", "mp3_close", "noop"} or (event.data or "").startswith("mp3_cancel:"):
                return await handler(event, data)
        from app.bot.commands import _check_channel_gate, _gate_keyboard, _gate_text
        try:
            missing = await _check_channel_gate(data["bot"], user.id, is_premium=getattr(user, "is_premium", False) is True)
        except Exception as exc:
            logger.warning("channel_gate feature unavailable user_id=%s error_type=%s", user.id, type(exc).__name__)
            missing = None
        if missing == []:
            return await handler(event, data)
        text = "Не удалось проверить условия доступа. Повторите позже." if missing is None else _gate_text(missing)
        if isinstance(event, InlineQuery):
            await event.answer([], is_personal=True, cache_time=0,
                               switch_pm_text="Проверьте условия доступа в боте", switch_pm_parameter="access")
        elif isinstance(event, ChosenInlineResult):
            return None
        elif isinstance(event, CallbackQuery):
            await event.answer("Откройте бота и выполните условия доступа.", show_alert=True)
            await data["bot"].send_message(user.id, text, parse_mode="HTML", reply_markup=_gate_keyboard(missing or []))
        elif isinstance(event, Message):
            await event.answer(text, parse_mode="HTML", reply_markup=_gate_keyboard(missing or []))
        return None
