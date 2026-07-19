"""Admin panel: Stars subscription management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.security import require_login
from app.database.session import get_db_session
from app.logging_config import get_logger
from app.repositories.subscription_repository import SubscriptionRepository

logger = get_logger(__name__)
router = APIRouter(tags=["dashboard-subscriptions"])
templates = Jinja2Templates(directory="app/dashboard/templates")


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    page: int = 1,
    status: str = "",
    success: str = "",
    error: str = "",
):
    redirect = require_login(request)
    if redirect:
        return redirect

    repo = SubscriptionRepository(session)
    config = await repo.get_config()
    data = await repo.list_subscribers(
        page=page,
        page_size=25,
        status_filter=status or None,
    )
    stats = await repo.get_stats()

    total_pages = max(1, (data["total"] + 24) // 25)

    return templates.TemplateResponse(
        request,
        "subscriptions.html",
        {
            "authenticated": True,
            "active_nav": "subscriptions",
            "config": config,
            "subscribers": data["subscribers"],
            "total": data["total"],
            "page": page,
            "total_pages": total_pages,
            "status_filter": status,
            "stats": stats,
            "success": success,
            "error": error,
        },
    )


@router.post("/subscriptions/config")
async def subscriptions_config_update(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    is_enabled: str = Form(default="off"),
    price_stars: int = Form(...),
    duration_days: int = Form(...),
    title: str = Form(...),
    description: str = Form(...),
    daily_multiplier: float = Form(default=1.0),
    daily_bonus_coins: int = Form(default=0),
    pet_feed_free: str = Form(default="off"),
    xp_multiplier: float = Form(default=1.0),
    max_pets_bonus: int = Form(default=0),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    repo = SubscriptionRepository(session)
    try:
        await repo.update_config(
            is_enabled=(is_enabled == "on"),
            price_stars=max(1, price_stars),
            duration_days=max(1, duration_days),
            title=title.strip() or "Premium подписка",
            description=description.strip(),
            benefits={
                "daily_multiplier":  max(1.0, daily_multiplier),
                "daily_bonus_coins": max(0, daily_bonus_coins),
                "pet_feed_free":     (pet_feed_free == "on"),
                "xp_multiplier":     max(1.0, xp_multiplier),
                "max_pets_bonus":    max(0, max_pets_bonus),
            },
        )
        await session.commit()
        return RedirectResponse("/subscriptions?success=Настройки+сохранены", status_code=303)
    except Exception as exc:
        logger.exception("subscriptions_config_update failed: %s", exc)
        return RedirectResponse("/subscriptions?error=Ошибка+сохранения", status_code=303)


@router.post("/subscriptions/grant")
async def subscriptions_grant(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    user_telegram_id: int = Form(...),
    duration_days: int = Form(default=30),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    repo = SubscriptionRepository(session)
    try:
        await repo.grant(user_telegram_id, duration_days)
        await session.commit()
        return RedirectResponse(
            f"/subscriptions?success=Подписка+выдана+пользователю+{user_telegram_id}",
            status_code=303,
        )
    except Exception as exc:
        logger.exception("subscriptions_grant failed: %s", exc)
        return RedirectResponse("/subscriptions?error=Ошибка+выдачи+подписки", status_code=303)


@router.post("/subscriptions/{user_telegram_id}/revoke")
async def subscriptions_revoke(
    request: Request,
    user_telegram_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    redirect = require_login(request)
    if redirect:
        return redirect

    repo = SubscriptionRepository(session)
    try:
        await repo.revoke(user_telegram_id)
        await session.commit()
        return RedirectResponse(
            f"/subscriptions?success=Подписка+пользователя+{user_telegram_id}+отозвана",
            status_code=303,
        )
    except Exception as exc:
        logger.exception("subscriptions_revoke failed: %s", exc)
        return RedirectResponse("/subscriptions?error=Ошибка+отзыва+подписки", status_code=303)
