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


@router.get("/app", response_model=None)
async def miniapp_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "miniapp.html", {})


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
