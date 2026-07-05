"""Data export routes (JSON / CSV)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.routes.messages import _build_filters
from app.dashboard.security import require_login
from app.database.session import get_db_session
from app.repositories.message_repository import MessageRepository
from app.services.export_service import ExportService

router = APIRouter(tags=["dashboard-export"])

EXPORT_LIMIT = 10_000


@router.get("/export", response_model=None)
async def export_messages(
    request: Request,
    format: Literal["json", "csv"] = "json",
    session: AsyncSession = Depends(get_db_session),
) -> Response | RedirectResponse:
    redirect = require_login(request)
    if redirect:
        return redirect

    filters = _build_filters(request)
    repo = MessageRepository(session)
    items, _ = await repo.search(filters, page=1, page_size=EXPORT_LIMIT)

    export_service = ExportService()
    content, media_type, filename = export_service.export(items, format)

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
