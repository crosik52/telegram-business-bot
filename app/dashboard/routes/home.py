"""Dashboard homepage route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.security import require_login
from app.database.session import get_db_session
from app.repositories.message_repository import MessageFilters, MessageRepository
from app.services.stats_service import StatsService

router = APIRouter(tags=["dashboard-home"])
templates = Jinja2Templates(directory="app/dashboard/templates")


@router.get("/", response_model=None)
async def dashboard_home(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse | RedirectResponse:
    redirect = require_login(request)
    if redirect:
        return redirect

    stats_service = StatsService(session)
    stats = await stats_service.get_dashboard_stats()

    message_repo = MessageRepository(session)
    recent_messages, _ = await message_repo.search(MessageFilters(), page=1, page_size=8)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "authenticated": True,
            "active_nav": "dashboard",
            "stats": stats,
            "recent_messages": recent_messages,
        },
    )
