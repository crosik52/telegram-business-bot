"""Admin panel: manage required subscription channels + broadcast."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.dispatcher import get_bot
from app.config import get_settings
from app.dashboard.security import require_login
from app.database.session import get_db_session
from app.logging_config import get_logger
from app.repositories.channel_repository import ChannelRepository

logger = get_logger(__name__)
router = APIRouter(tags=["dashboard-channels"])
templates = Jinja2Templates(directory="app/dashboard/templates")
settings = get_settings()


# ── helpers ──────────────────────────────────────────────────────────────────

async def _fetch_channel_title(bot, username: str) -> str | None:
    """Try to resolve the channel title via Bot API."""
    try:
        chat = await bot.get_chat(username)
        return getattr(chat, "title", None)
    except Exception:
        return None


# ── routes ───────────────────────────────────────────────────────────────────

@router.get("/channels", response_class=HTMLResponse)
async def channels_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    success: str = "",
    error: str = "",
):
    redirect = require_login(request)
    if redirect:
        return redirect

    repo = ChannelRepository(session)
    channels = await repo.get_all()

    return templates.TemplateResponse(
        request,
        "channels.html",
        {
            "authenticated": True,
            "active_nav": "channels",
            "channels": channels,
            "success": success,
            "error": error,
        },
    )


@router.post("/channels/add")
async def channels_add(
    request: Request,
    channel_username: str = Form(...),
    session: AsyncSession = Depends(get_db_session),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    username = channel_username.strip()
    if not username:
        return RedirectResponse("/channels?error=Введите+username+канала", status_code=303)

    # Normalise — keep @username or numeric -100…
    if not username.startswith("-") and not username.startswith("@"):
        username = f"@{username}"

    # Try to fetch title from Telegram
    bot = get_bot(settings)
    title = await _fetch_channel_title(bot, username) if bot else None

    repo = ChannelRepository(session)
    try:
        await repo.add(username, title)
        await session.commit()
        label = title or username
        return RedirectResponse(f"/channels?success=Канал+«{label}»+добавлен", status_code=303)
    except Exception as exc:
        logger.error("channels_add: %s", exc)
        return RedirectResponse("/channels?error=Канал+уже+существует+или+ошибка+БД", status_code=303)


@router.post("/channels/{channel_id}/toggle")
async def channels_toggle(
    request: Request,
    channel_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    repo = ChannelRepository(session)
    await repo.toggle(channel_id)
    await session.commit()
    return RedirectResponse("/channels", status_code=303)


@router.post("/channels/{channel_id}/delete")
async def channels_delete(
    request: Request,
    channel_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    repo = ChannelRepository(session)
    await repo.delete(channel_id)
    await session.commit()
    return RedirectResponse("/channels", status_code=303)


@router.post("/channels/broadcast")
async def channels_broadcast(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    """Send a subscription-reminder DM to all non-blocked users."""
    redirect = require_login(request)
    if redirect:
        return redirect

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415
    from app.models.business_connection import BusinessConnection  # noqa: PLC0415

    repo = ChannelRepository(session)
    active_channels = await repo.get_active()

    if not active_channels:
        return RedirectResponse("/channels?error=Нет+активных+каналов", status_code=303)

    bot = get_bot(settings)
    if not bot:
        return RedirectResponse("/channels?error=Бот+не+инициализирован", status_code=303)

    # Build message
    channel_lines = "\n".join(
        f"• {ch.display_title} ({ch.at_username})" for ch in active_channels
    )
    text = (
        "📢 <b>Для использования бота необходимо подписаться на каналы:</b>\n\n"
        f"{channel_lines}\n\n"
        "После подписки все функции будут доступны автоматически."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📢 {ch.display_title}", url=ch.join_url)]
        for ch in active_channels
    ])

    # Get all non-blocked users
    result = await session.execute(
        select(BusinessConnection.user_telegram_id)
        .where(
            BusinessConnection.is_blocked.is_(False),
            BusinessConnection.is_enabled.is_(True),
        )
        .distinct()
    )
    user_ids = [row[0] for row in result.all()]

    sent = 0
    failed = 0

    async def _send(uid: int) -> None:
        nonlocal sent, failed
        try:
            await bot.send_message(uid, text, parse_mode="HTML", reply_markup=keyboard)
            sent += 1
        except Exception as exc:
            failed += 1
            logger.debug("broadcast to %s failed: %s", uid, exc)

    # Send with a small delay between messages to avoid flood limits
    for uid in user_ids:
        await _send(uid)
        await asyncio.sleep(0.05)

    logger.info("Channel broadcast: sent=%d failed=%d", sent, failed)
    return RedirectResponse(
        f"/channels?success=Рассылка+отправлена:+{sent}+доставлено,+{failed}+ошибок",
        status_code=303,
    )
