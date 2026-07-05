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
                "display_name": s.display_name,
                "username": s.username,
                "message_count": s.message_count,
                "edited_count": s.edited_count,
                "deleted_count": s.deleted_count,
                "last_message_at": (
                    s.last_message_at.isoformat() if s.last_message_at else None
                ),
            }
            for s in stats.top_interlocutors
        ],
    }


class AdminOverviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")


@router.post("/app/api/admin/overview")
async def admin_overview(
    payload: AdminOverviewRequest, session: AsyncSession = Depends(get_db_session)
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
