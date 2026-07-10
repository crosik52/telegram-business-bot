"""Message browsing, search/filter, and detail routes."""

from __future__ import annotations

import datetime as dt
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.security import require_login
from app.database.session import get_db_session
from app.repositories.chat_settings_repository import ChatSettingsRepository
from app.repositories.contact_note_repository import ContactNoteRepository
from app.repositories.message_repository import MessageFilters, MessageRepository
from app.utils.pagination import Page

router = APIRouter(tags=["dashboard-messages"])
templates = Jinja2Templates(directory="app/dashboard/templates")

PAGE_SIZE = 25


def _parse_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=dt.UTC)
    except ValueError:
        return None


def _build_filters(request: Request) -> MessageFilters:
    params = request.query_params
    date_from = _parse_date(params.get("date_from"))
    date_to = _parse_date(params.get("date_to"))
    if date_to is not None:
        date_to = date_to + dt.timedelta(days=1) - dt.timedelta(seconds=1)

    return MessageFilters(
        text_query=params.get("q") or None,
        username=params.get("username") or None,
        chat_id=int(params["chat_id"]) if params.get("chat_id") else None,
        date_from=date_from,
        date_to=date_to,
        only_edited=params.get("edited") == "1",
        only_deleted=params.get("deleted") == "1",
        only_media=params.get("media") == "1",
        only_text=params.get("text_only") == "1",
    )


@router.get("/messages", response_model=None)
async def list_messages(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse | RedirectResponse:
    redirect = require_login(request)
    if redirect:
        return redirect

    filters = _build_filters(request)
    page_num = int(request.query_params.get("page", 1))

    repo = MessageRepository(session)
    items, total = await repo.search(filters, page=page_num, page_size=PAGE_SIZE)
    page = Page(items=items, page=page_num, page_size=PAGE_SIZE, total=total)

    qs_params = {
        k: v
        for k, v in request.query_params.items()
        if k not in ("page",) and v
    }
    query_suffix = ("&" + urlencode(qs_params)) if qs_params else ""

    return templates.TemplateResponse(
        request,
        "messages.html",
        {
            "authenticated": True,
            "active_nav": "messages",
            "page": page,
            "filters": filters,
            "date_from_str": request.query_params.get("date_from", ""),
            "date_to_str": request.query_params.get("date_to", ""),
            "query_suffix": query_suffix,
            "export_qs": ("&" + urlencode(qs_params)) if qs_params else "",
        },
    )


@router.get("/messages/deleted", response_model=None)
async def deleted_messages(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> HTMLResponse | RedirectResponse:
    redirect = require_login(request)
    if redirect:
        return redirect

    page_num = int(request.query_params.get("page", 1))
    repo = MessageRepository(session)
    items, total = await repo.get_deleted(page=page_num, page_size=PAGE_SIZE)
    page = Page(items=items, page=page_num, page_size=PAGE_SIZE, total=total)

    return templates.TemplateResponse(
        request,
        "deleted.html",
        {"authenticated": True, "active_nav": "deleted", "page": page},
    )


@router.get("/messages/{message_id}", response_model=None)
async def message_detail(
    message_id: int,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> HTMLResponse | RedirectResponse:
    redirect = require_login(request)
    if redirect:
        return redirect

    repo = MessageRepository(session)
    message = await repo.get_by_id(message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")

    # Load contact notes and mute status for this chat.
    note_repo = ContactNoteRepository(session)
    notes = await note_repo.get_for_chat(
        message.business_connection_id, message.chat_id
    )

    chat_repo = ChatSettingsRepository(session)
    chat_settings = await chat_repo.get(
        message.business_connection_id, message.chat_id
    )

    return templates.TemplateResponse(
        request,
        "message_detail.html",
        {
            "authenticated": True,
            "active_nav": "messages",
            "message": message,
            "contact_notes": notes,
            "chat_settings": chat_settings,
        },
    )
