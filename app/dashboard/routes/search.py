"""Chat search route — find matching messages across all chats."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.security import require_login
from app.database.session import get_db_session
from app.repositories.message_repository import MessageRepository

router = APIRouter(tags=["dashboard-search"])
templates = Jinja2Templates(directory="app/dashboard/templates")


@router.get("/search", response_model=None)
async def chat_search(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse | RedirectResponse:
    redirect = require_login(request)
    if redirect:
        return redirect

    query = request.query_params.get("q", "").strip()
    results: list[dict] = []
    error: str | None = None

    if query:
        if len(query) < 2:
            error = "Введите минимум 2 символа."
        else:
            repo = MessageRepository(session)
            try:
                results = await repo.search_chats(query, limit=100)
            except Exception:
                error = "Ошибка при поиске. Попробуйте снова."

    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "authenticated": True,
            "active_nav": "search",
            "query": query,
            "results": results,
            "error": error,
        },
    )
