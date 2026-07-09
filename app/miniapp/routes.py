"""Routes for the personal Telegram Mini App (no admin login required)."""

from __future__ import annotations

import datetime as dt

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
from app.models.admin_action_log import AdminActionLog
from app.models.business_connection import BusinessConnection
from app.repositories.message_repository import MessageFilters, MessageRepository
from app.repositories.wallet_repository import WalletRepository
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


class AdminUserStatsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    owner_telegram_id: int = Field(alias="ownerTelegramId")


class AdminMessagesRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    owner_telegram_id: int = Field(alias="ownerTelegramId")
    chat_id: int = Field(alias="chatId")
    page: int = Field(default=1)


class AdminBroadcastRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    text: str


class AdminActionLogRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    page: int = Field(default=1)


GAME_EMOJIS = {"🎲", "🎯", "🏀", "⚽", "🎳", "🎰"}


def _compute_badges(stats) -> list[dict]:
    """Rule-based achievement badges computed from already-aggregated
    per-owner stats — no extra DB queries needed."""

    # Use global streak fields (computed across ALL chats, not just top_n) so
    # streaks in lower-volume chats are correctly reflected in badges.
    best_streak = stats.best_streak
    best_longest = stats.global_longest_streak

    definitions = [
        ("🎉", "Первые шаги", "Отправлено первое сообщение", stats.total_messages >= 1),
        ("💬", "Активный собеседник", "100+ сообщений", stats.total_messages >= 100),
        ("🏆", "Мастер переписки", "1 000+ сообщений", stats.total_messages >= 1000),
        ("🌐", "Душа компании", "5+ разных чатов", stats.total_chats >= 5),
        ("🔥", "Не разлей вода", "Серия 7+ дней подряд", best_streak >= 7),
        ("🚀", "Марафонец", "Серия 30+ дней подряд", best_streak >= 30),
        ("💎", "Легенда", "Серия 100+ дней подряд", best_longest >= 100),
        (
            "🕵️",
            "Внимание к деталям",
            "10+ отредактированных сообщений",
            stats.edited_messages >= 10,
        ),
    ]
    return [
        {"emoji": e, "title": t, "description": d, "achieved": achieved}
        for e, t, d, achieved in definitions
    ]


class GameRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    chat_id: int = Field(alias="chatId")
    emoji: str


class StreakRemindRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    chat_id: int = Field(alias="chatId")
    streak_days: int = Field(alias="streakDays")


class WalletRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")


class ClaimDailyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")


class SlotSpinRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")


class FlipRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    bet: int
    choice: str  # "heads" or "tails"


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
            "badges": [],
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
            "best_streak": stats.best_streak,
            "best_streak_name": stats.best_streak_name,
            "global_longest_streak": stats.global_longest_streak,
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
                    "longest_streak": s.longest_streak,
                    "mutual_connected": s.mutual_connected,
                }
                for s in stats.top_interlocutors
            ],
            "badges": _compute_badges(stats),
        }
    except Exception:
        logger.exception(
            "Failed to build owner stats for owner_telegram_id=%s", owner_telegram_id
        )
        raise HTTPException(status_code=500, detail="Failed to load stats") from None


@router.post("/app/api/activity")
async def miniapp_activity(
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
        return {"days": 90, "activity": {}}

    try:
        stats_service = StatsService(session)
        activity = await stats_service.get_owner_activity(
            connection_ids=connection_ids, days=90
        )
        return {"days": 90, "activity": activity}
    except Exception:
        logger.exception(
            "Failed to build activity for owner_telegram_id=%s", owner_telegram_id
        )
        raise HTTPException(status_code=500, detail="Failed to load activity") from None


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


@router.post("/app/api/streak/remind")
async def streak_remind(
    payload: StreakRemindRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Send a streak-reminder message from the caller to a mutual contact.

    Both parties must have an active business connection to the bot and the
    caller must have an existing chat with the counterpart (authorization check).
    """
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_telegram_id = int(user["id"])

    # Caller's active connections
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

    # Counterpart must also be a connected bot user
    counterpart_result = await session.execute(
        select(BusinessConnection.business_connection_id).where(
            BusinessConnection.user_telegram_id == payload.chat_id,
            BusinessConnection.is_blocked.is_(False),
        )
    )
    if counterpart_result.first() is None:
        raise HTTPException(status_code=403, detail="Counterpart is not connected to the bot")

    # Caller must actually have this chat in their history
    stats_service = StatsService(session)
    owns_chat = await stats_service.owner_has_chat(
        connection_ids=connection_ids, chat_id=payload.chat_id
    )
    if not owns_chat:
        raise HTTPException(status_code=403, detail="No shared chat with this user")

    streak = max(payload.streak_days, 0)
    text = _build_streak_remind_text(streak)

    from app.business.dispatcher import get_bot

    bot = get_bot(settings)
    try:
        await bot.send_message(
            business_connection_id=connection_ids[0],
            chat_id=payload.chat_id,
            text=text,
        )
    except Exception as exc:
        logger.warning(
            "Failed to send streak reminder to chat_id=%s: %s", payload.chat_id, exc
        )
        raise HTTPException(status_code=502, detail="Failed to send reminder") from exc

    return {"sent": True, "streak_days": streak}


def _build_streak_remind_text(streak_days: int) -> str:
    """Pick a reminder message based on how long the streak already is."""
    days = streak_days
    if days >= 100:
        return (
            f"💎 {days} дней общения подряд — это легенда! "
            "Пишу, чтобы не прерывать наш рекорд 👑"
        )
    if days >= 30:
        return (
            f"🚀 {days} дней подряд! Это уже марафон — "
            "не хочу останавливаться 💪"
        )
    if days >= 14:
        return (
            f"🔥🔥 Уже {days} дней подряд общаемся! "
            "Напоминаю о себе, чтобы серия не прервалась 😄"
        )
    if days >= 7:
        return (
            f"🔥 Неделя или больше подряд — {days} дней! "
            "Держим стрик? Напиши что-нибудь 😊"
        )
    if days >= 2:
        return (
            f"👋 У нас уже {days} дня подряд! "
            "Напоминаю о себе — держим серию? 🔥"
        )
    return "👋 Привет! Просто напоминаю о себе 😊"


@router.post("/app/api/wallet/info")
async def wallet_info(
    payload: WalletRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    owner_id = int(user["id"])
    repo = WalletRepository(session)
    wallet = await repo.get_or_create(owner_id)
    can_claim, secs = repo.daily_claim_status(wallet)
    return {
        "balance": wallet.balance,
        "total_earned": wallet.total_earned,
        "total_spent": wallet.total_spent,
        "can_claim_daily": can_claim,
        "seconds_until_next_claim": secs,
    }


@router.post("/app/api/wallet/claim_daily")
async def wallet_claim_daily(
    payload: ClaimDailyRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    owner_id = int(user["id"])

    # Derive streak server-side — never trust client-supplied values for reward math.
    streak_days = 0
    conn_result = await session.execute(
        select(BusinessConnection).where(
            BusinessConnection.user_telegram_id == owner_id,
            BusinessConnection.is_blocked.is_(False),
        )
    )
    connections = conn_result.scalars().all()
    if connections:
        connection_ids = [c.business_connection_id for c in connections]
        stats_service = StatsService(session)
        owner_stats = await stats_service.get_owner_stats(
            owner_telegram_id=owner_id,
            connection_ids=connection_ids,
            top_n=1,
        )
        streak_days = max(0, owner_stats.best_streak or 0)

    repo = WalletRepository(session)
    try:
        result = await repo.claim_daily(owner_id, streak_days=streak_days)
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    return {
        "earned": result.earned,
        "base": result.base,
        "streak_bonus": result.streak_bonus,
        "new_balance": result.new_balance,
    }


@router.post("/app/api/wallet/slots")
async def wallet_slots(
    payload: SlotSpinRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    owner_id = int(user["id"])
    repo = WalletRepository(session)
    try:
        result = await repo.spin_slots(owner_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "reels": result.reels,
        "payout": result.payout,
        "net": result.net,
        "is_jackpot": result.is_jackpot,
        "new_balance": result.new_balance,
    }


@router.post("/app/api/wallet/flip")
async def wallet_flip(
    payload: FlipRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    if payload.choice not in ("heads", "tails"):
        raise HTTPException(status_code=400, detail="choice must be heads or tails")
    owner_id = int(user["id"])
    repo = WalletRepository(session)
    try:
        result = await repo.flip_coin(owner_id, payload.bet, payload.choice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "server_side": result.server_side,
        "won": result.won,
        "amount_change": result.amount_change,
        "new_balance": result.new_balance,
    }


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


@router.post("/app/api/admin/user_stats")
async def admin_user_stats(
    payload: AdminUserStatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    _require_admin(payload.init_data)

    result = await session.execute(
        select(BusinessConnection.business_connection_id).where(
            BusinessConnection.user_telegram_id == payload.owner_telegram_id
        )
    )
    connection_ids = [row[0] for row in result.all()]

    if not connection_ids:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        stats_service = StatsService(session)
        stats = await stats_service.get_owner_stats(
            connection_ids=connection_ids,
            owner_telegram_id=payload.owner_telegram_id,
            top_n=50,
        )

        return {
            "owner_telegram_id": stats.owner_telegram_id,
            "total_messages": stats.total_messages,
            "total_chats": stats.total_chats,
            "edited_messages": stats.edited_messages,
            "deleted_messages": stats.deleted_messages,
            "chats": [
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
            "Failed to build admin user stats for owner_telegram_id=%s",
            payload.owner_telegram_id,
        )
        raise HTTPException(status_code=500, detail="Failed to load user stats") from None


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

    session.add(
        AdminActionLog(
            admin_username=admin_user.get("username"),
            action="update_settings",
            target_owner_telegram_id=payload.owner_telegram_id,
            details=(
                f"notifications_enabled={payload.notifications_enabled}, "
                f"is_blocked={payload.is_blocked}"
            ),
        )
    )
    await session.commit()

    logger.info(
        "Admin @%s updated settings for owner_telegram_id=%s (rows=%s)",
        admin_user.get("username"),
        payload.owner_telegram_id,
        updated,
    )

    return {"updated_connections": updated}


@router.post("/app/api/admin/growth")
async def admin_growth(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    _require_admin(payload.init_data)

    stats_service = StatsService(session)
    growth = await stats_service.get_admin_growth(days=30)
    return {"days": 30, **growth}


@router.post("/app/api/admin/messages")
async def admin_messages(
    payload: AdminMessagesRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    _require_admin(payload.init_data)

    result = await session.execute(
        select(BusinessConnection.business_connection_id).where(
            BusinessConnection.user_telegram_id == payload.owner_telegram_id
        )
    )
    connection_ids = [row[0] for row in result.all()]
    if not connection_ids:
        raise HTTPException(status_code=404, detail="User not found")

    page_size = 30
    try:
        repo = MessageRepository(session)
        filters = MessageFilters(chat_id=payload.chat_id, connection_ids=connection_ids)
        items, total = await repo.search(filters, page=payload.page, page_size=page_size)

        return {
            "page": payload.page,
            "page_size": page_size,
            "total": total,
            "messages": [
                {
                    "id": m.id,
                    "sender_first_name": m.sender_first_name,
                    "sender_last_name": m.sender_last_name,
                    "sender_username": m.sender_username,
                    "sender_telegram_id": m.sender_telegram_id,
                    "text": m.text,
                    "caption": m.caption,
                    "media_type": m.media_type.value,
                    "is_edited": m.is_edited,
                    "is_deleted": m.is_deleted,
                    "edit_count": m.edit_count,
                    "sent_at": m.sent_at.isoformat() if m.sent_at else None,
                    "deleted_at": m.deleted_at.isoformat() if m.deleted_at else None,
                }
                for m in items
            ],
        }
    except Exception:
        logger.exception(
            "Failed to load admin messages for owner_telegram_id=%s chat_id=%s",
            payload.owner_telegram_id,
            payload.chat_id,
        )
        raise HTTPException(status_code=500, detail="Failed to load messages") from None


@router.post("/app/api/admin/broadcast")
async def admin_broadcast(
    payload: AdminBroadcastRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    admin_user = _require_admin(payload.init_data)

    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty broadcast text")

    result = await session.execute(
        select(BusinessConnection.user_telegram_id)
        .where(BusinessConnection.is_blocked.is_(False))
        .distinct()
    )
    owner_ids = [row[0] for row in result.all()]

    from app.business.dispatcher import get_bot

    settings = get_settings()
    bot = get_bot(settings)

    sent = 0
    failed = 0
    for owner_id in owner_ids:
        try:
            await bot.send_message(chat_id=owner_id, text=text)
            sent += 1
        except Exception as exc:  # noqa: BLE001 - one failed recipient shouldn't stop the rest
            failed += 1
            logger.warning("Broadcast failed for owner_telegram_id=%s: %s", owner_id, exc)

    session.add(
        AdminActionLog(
            admin_username=admin_user.get("username"),
            action="broadcast",
            details=f"text={text!r}, sent={sent}, failed={failed}",
        )
    )
    await session.commit()

    logger.info(
        "Admin @%s sent broadcast to %s users (%s failed)",
        admin_user.get("username"),
        sent,
        failed,
    )

    return {"sent": sent, "failed": failed, "total_recipients": len(owner_ids)}


@router.post("/app/api/admin/action_log")
async def admin_action_log(
    payload: AdminActionLogRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    _require_admin(payload.init_data)

    page_size = 30
    from sqlalchemy import func as sa_func

    total = (
        await session.execute(select(sa_func.count(AdminActionLog.id)))
    ).scalar_one()

    stmt = (
        select(AdminActionLog)
        .order_by(AdminActionLog.created_at.desc())
        .offset((payload.page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()

    return {
        "page": payload.page,
        "page_size": page_size,
        "total": int(total),
        "entries": [
            {
                "id": r.id,
                "admin_username": r.admin_username,
                "action": r.action,
                "target_owner_telegram_id": r.target_owner_telegram_id,
                "details": r.details,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }
