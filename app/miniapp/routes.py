"""Routes for the personal Telegram Mini App (no admin login required)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.session import get_db_session
from app.logging_config import get_logger
from app.miniapp.auth import verify_init_data
from app.models.business_connection import BusinessConnection
from app.services.stats_service import StatsService

logger = get_logger(__name__)
router = APIRouter(tags=["miniapp"])
templates = Jinja2Templates(directory="app/miniapp/templates")


class StatsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")


class AdminSettingsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    owner_telegram_id: int = Field(alias="ownerTelegramId")
    notifications_enabled: bool | None = Field(default=None, alias="notificationsEnabled")
    is_blocked: bool | None = Field(default=None, alias="isBlocked")


GAME_EMOJIS = {"🎲", "🎯", "🏀", "⚽", "🎳", "🎰"}


class GameRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    chat_id: int = Field(alias="chatId")
    emoji: str


def _require_admin(init_data: str) -> dict:
    """Verify initData and ensure the caller's Telegram @username matches the
    configured mini app super-admin. Returns the verified user dict."""

    settings = get_settings()
    user = verify_init_data(init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    username = (user.get("username") or "").lstrip("@").lower()
    admin_username = settings.miniapp_admin_username.lstrip("@").lower()
    if not admin_username or username != admin_username:
        raise HTTPException(status_code=403, detail="Not authorized")

    return user


@router.get("/app", response_model=None)
async def miniapp_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "miniapp.html", {})


@router.get("/app/admin", response_model=None)
async def admin_page(request: Request) -> HTMLResponse:
    # The page itself is static HTML; the real auth check happens on every
    # API call below via signed initData, so an unauthorized user only ever
    # sees an "access denied" message rendered client-side.
    return templates.TemplateResponse(request, "admin.html", {})


@router.post("/app/api/stats")
async def miniapp_stats(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_telegram_id = int(user["id"])

    result = await session.execute(
        select(BusinessConnection.business_connection_id).where(
            BusinessConnection.user_telegram_id == owner_telegram_id
        )
    )
    connection_ids = [row[0] for row in result.all()]

    if not connection_ids:
        return {
            "connected": False,
            "total_messages": 0,
            "total_chats": 0,
            "edited_messages": 0,
            "deleted_messages": 0,
            "top_interlocutors": [],
        }

    try:
        stats_service = StatsService(session)
        stats = await stats_service.get_owner_stats(
            connection_ids=connection_ids, owner_telegram_id=owner_telegram_id
        )

        return {
            "connected": True,
            "total_messages": stats.total_messages,
            "total_chats": stats.total_chats,
            "edited_messages": stats.edited_messages,
            "deleted_messages": stats.deleted_messages,
            "top_interlocutors": [
                {
                    "chat_id": s.chat_id,
                    "display_name": s.display_name,
                    "username": s.username,
                    "message_count": s.message_count,
                    "edited_count": s.edited_count,
                    "deleted_count": s.deleted_count,
                    "last_message_at": (
                        s.last_message_at.isoformat() if s.last_message_at else None
                    ),
                    "streak_days": s.streak_days,
                    "mutual_connected": s.mutual_connected,
                }
                for s in stats.top_interlocutors
            ],
        }
    except Exception:
        logger.exception(
            "Failed to build owner stats for owner_telegram_id=%s", owner_telegram_id
        )
        raise HTTPException(status_code=500, detail="Failed to load stats") from None


@router.post("/app/api/game/send")
async def send_game(
    payload: GameRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_telegram_id = int(user["id"])

    if payload.emoji not in GAME_EMOJIS:
        raise HTTPException(status_code=400, detail="Unsupported game")

    result = await session.execute(
        select(BusinessConnection).where(
            BusinessConnection.user_telegram_id == owner_telegram_id,
            BusinessConnection.is_blocked.is_(False),
        )
    )
    connections = result.scalars().all()
    if not connections:
        raise HTTPException(status_code=403, detail="No active business connection")
    connection_ids = [c.business_connection_id for c in connections]

    # The counterpart must also be an active bot user (mutual connection) —
    # otherwise there is no chat we're allowed to inject a game message into.
    counterpart_result = await session.execute(
        select(BusinessConnection.business_connection_id).where(
            BusinessConnection.user_telegram_id == payload.chat_id,
            BusinessConnection.is_blocked.is_(False),
        )
    )
    if counterpart_result.first() is None:
        raise HTTPException(status_code=403, detail="Counterpart is not connected")

    stats_service = StatsService(session)
    owns_chat = await stats_service.owner_has_chat(
        connection_ids=connection_ids, chat_id=payload.chat_id
    )
    if not owns_chat:
        raise HTTPException(status_code=403, detail="No shared chat with this user")

    from app.business.dispatcher import get_bot

    bot = get_bot(settings)
    try:
        sent = await bot.send_dice(
            business_connection_id=connection_ids[0],
            chat_id=payload.chat_id,
            emoji=payload.emoji,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the mini app as a generic failure
        logger.warning(
            "Failed to send game dice to chat_id=%s: %s", payload.chat_id, exc
        )
        raise HTTPException(status_code=502, detail="Failed to send game") from exc

    value = sent.dice.value if sent.dice else None
    return {"sent": True, "value": value}


@router.post("/app/api/admin/overview")
async def admin_overview(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    _require_admin(payload.init_data)

    stats_service = StatsService(session)
    overview = await stats_service.get_admin_overview()

    return {
        "total_users": overview.total_users,
        "users": [
            {
                "owner_telegram_id": u.owner_telegram_id,
                "username": u.username,
                "first_name": u.first_name,
                "last_name": u.last_name,
                "connected_at": u.connected_at.isoformat(),
                "is_enabled": u.is_enabled,
                "can_reply": u.can_reply,
                "notifications_enabled": u.notifications_enabled,
                "is_blocked": u.is_blocked,
                "total_messages": u.total_messages,
                "total_chats": u.total_chats,
                "edited_messages": u.edited_messages,
                "deleted_messages": u.deleted_messages,
                "last_activity_at": (
                    u.last_activity_at.isoformat() if u.last_activity_at else None
                ),
            }
            for u in overview.users
        ],
    }


@router.post("/app/api/admin/settings")
async def admin_settings(
    payload: AdminSettingsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    admin_user = _require_admin(payload.init_data)

    if payload.notifications_enabled is None and payload.is_blocked is None:
        raise HTTPException(status_code=400, detail="No settings provided")

    stats_service = StatsService(session)
    updated = await stats_service.set_owner_settings(
        owner_telegram_id=payload.owner_telegram_id,
        notifications_enabled=payload.notifications_enabled,
        is_blocked=payload.is_blocked,
    )
    await session.commit()

    logger.info(
        "Admin @%s updated settings for owner_telegram_id=%s (rows=%s)",
        admin_user.get("username"),
        payload.owner_telegram_id,
        updated,
    )

    return {"updated_connections": updated}
