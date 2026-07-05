"""Statistics page route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.security import require_login
from app.database.session import get_db_session
from app.services.stats_service import StatsService

router = APIRouter(tags=["dashboard-stats"])
templates = Jinja2Templates(directory="app/dashboard/templates")


@router.get("/statistics", response_model=None)
async def statistics_page(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse | RedirectResponse:
    redirect = require_login(request)
    if redirect:
        return redirect

    stats_service = StatsService(session)
    stats = await stats_service.get_dashboard_stats()

    return templates.TemplateResponse(
        request,
        "statistics.html",
        {"authenticated": True, "active_nav": "statistics", "stats": stats},
    )
