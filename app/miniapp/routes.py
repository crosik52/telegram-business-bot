"""Routes for the personal Telegram Mini App (no admin login required)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.session import get_db_session
from app.logging_config import get_logger
from app.miniapp.auth import verify_init_data
from app.models.admin_action_log import AdminActionLog
from app.models.business_connection import BusinessConnection
from app.models.message import MediaType, Message
from app.repositories.message_repository import MessageFilters, MessageRepository
from app.repositories.pet_repository import FEED_COST, PetRepository
from app.repositories.quest_repository import QUESTS, QuestRepository
from app.repositories.wallet_repository import WalletRepository
from app.services.admin_chart_service import AdminStats, render_admin_image
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


class QuestClaimRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    quest_id: str = Field(alias="questId")


class AdminWalletSetRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    owner_telegram_id: int = Field(alias="ownerTelegramId")
    new_balance: int = Field(alias="newBalance", ge=0, le=10_000_000)


class PetAdoptRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    chat_id: int = Field(alias="chatId")
    species: str
    pet_name: str = Field(alias="petName", default="")


class PetFeedRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    pet_id: int = Field(alias="petId")


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
            "media_messages": 0,
            "media_breakdown": [],
            "top_interlocutors": [],
            "badges": [],
        }

    try:
        stats_service = StatsService(session)
        stats = await stats_service.get_owner_stats(
            connection_ids=connection_ids, owner_telegram_id=owner_telegram_id
        )

        # Media breakdown — one aggregate query, not loaded by get_owner_stats.
        # Exclude both NONE (unset) and TEXT so only true media types are counted.
        media_rows = (
            await session.execute(
                select(Message.media_type, func.count(Message.id))
                .where(
                    Message.business_connection_id.in_(connection_ids),
                    Message.media_type.notin_([MediaType.NONE, MediaType.TEXT]),
                )
                .group_by(Message.media_type)
            )
        ).all()
        media_messages = sum(r[1] for r in media_rows)
        media_breakdown = [
            {"type": r[0].value, "count": r[1]}
            for r in sorted(media_rows, key=lambda r: r[1], reverse=True)
        ]

        return {
            "connected": True,
            "total_messages": stats.total_messages,
            "total_chats": stats.total_chats,
            "edited_messages": stats.edited_messages,
            "deleted_messages": stats.deleted_messages,
            "media_messages": media_messages,
            "media_breakdown": media_breakdown,
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


# ── Quests ────────────────────────────────────────────────────────────────────

def _today_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


async def _today_activity(
    connection_ids: list[str], session
) -> tuple[int, int, bool]:
    """Return (today_messages, today_chats, has_streak).

    Streak = at least one message both yesterday and today (server-derived).
    """
    if not connection_ids:
        return 0, 0, False

    today_start = _today_utc()
    yesterday_start = today_start - dt.timedelta(days=1)

    act_row = (
        await session.execute(
            select(
                func.count(Message.id).label("m"),
                func.count(Message.chat_id.distinct()).label("c"),
            ).where(
                Message.business_connection_id.in_(connection_ids),
                Message.sent_at >= today_start,
                Message.is_deleted.is_(False),
            )
        )
    ).one()
    today_messages: int = act_row.m
    today_chats: int = act_row.c

    yest_count = (
        await session.execute(
            select(func.count(Message.id)).where(
                Message.business_connection_id.in_(connection_ids),
                Message.sent_at >= yesterday_start,
                Message.sent_at < today_start,
                Message.is_deleted.is_(False),
            )
        )
    ).scalar_one()
    has_streak = yest_count > 0 and today_messages > 0
    return today_messages, today_chats, has_streak


@router.post("/app/api/quests")
async def miniapp_quests(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Return today's quest list with per-user progress (all server-side)."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])
    conn_ids = [
        r[0]
        for r in (
            await session.execute(
                select(BusinessConnection.business_connection_id).where(
                    BusinessConnection.user_telegram_id == owner_id
                )
            )
        ).all()
    ]

    today_messages, today_chats, has_streak = await _today_activity(conn_ids, session)

    quest_repo = QuestRepository(session)
    claimed = await quest_repo.get_today_completions(owner_id)

    quests_out = []
    for q in QUESTS:
        if q["id"] == "MSG_5":
            progress, target = today_messages, 5
        elif q["id"] == "CHAT_2":
            progress, target = today_chats, 2
        else:  # STREAK
            progress, target = (1 if has_streak else 0), 1

        quests_out.append(
            {
                "id": q["id"],
                "emoji": q["emoji"],
                "title": q["title"],
                "desc": q["desc"],
                "reward": q["reward"],
                "progress": min(progress, target),
                "target": target,
                "claimed": q["id"] in claimed,
            }
        )

    return {
        "quests": quests_out,
        "today_messages": today_messages,
        "today_chats": today_chats,
    }


@router.post("/app/api/quests/claim")
async def miniapp_quest_claim(
    payload: QuestClaimRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Verify and claim a completed daily quest. Progress is always recomputed
    server-side — the client only supplies quest_id."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])
    conn_ids = [
        r[0]
        for r in (
            await session.execute(
                select(BusinessConnection.business_connection_id).where(
                    BusinessConnection.user_telegram_id == owner_id
                )
            )
        ).all()
    ]

    today_messages, today_chats, has_streak = await _today_activity(conn_ids, session)

    quest_repo = QuestRepository(session)
    try:
        reward = await quest_repo.claim_quest(
            owner_id,
            payload.quest_id,
            today_messages=today_messages,
            today_chats=today_chats,
            has_streak=has_streak,
        )
    except ValueError as exc:
        code = str(exc)
        status = 409 if code == "already_claimed" else 400
        raise HTTPException(status_code=status, detail=code) from exc

    await session.commit()

    repo = WalletRepository(session)
    wallet = await repo.get_or_create(owner_id)
    return {"ok": True, "reward": reward, "new_balance": wallet.balance}


@router.post("/app/api/leaderboard")
async def miniapp_leaderboard(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Top 15 users by total_earned coins + the current user's rank."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])

    from app.models.wallet import UserWallet  # local to avoid circular dep

    top_rows = (
        await session.execute(
            select(UserWallet.owner_telegram_id, UserWallet.balance, UserWallet.total_earned)
            .order_by(UserWallet.total_earned.desc())
            .limit(15)
        )
    ).all()

    top_ids = [r[0] for r in top_rows]
    names_map: dict[int, tuple] = {}
    if top_ids:
        name_rows = (
            await session.execute(
                select(
                    BusinessConnection.user_telegram_id,
                    BusinessConnection.user_first_name,
                    BusinessConnection.user_last_name,
                    BusinessConnection.user_username,
                )
                .where(BusinessConnection.user_telegram_id.in_(top_ids))
                .distinct(BusinessConnection.user_telegram_id)
            )
        ).all()
        names_map = {r[0]: (r[1], r[2], r[3]) for r in name_rows}

    entries = []
    my_rank: int | None = None
    for i, row in enumerate(top_rows):
        fn, ln, un = names_map.get(row[0], (None, None, None))
        name_parts = [p for p in (fn, ln) if p]
        display = " ".join(name_parts) if name_parts else (f"@{un}" if un else "Аноним")
        is_self = row[0] == owner_id
        if is_self:
            my_rank = i + 1
        entries.append(
            {
                "rank": i + 1,
                "display_name": display,
                "is_self": is_self,
                "balance": row[1],
                "total_earned": row[2],
            }
        )

    if my_rank is None:
        own_earned = (
            await session.execute(
                select(UserWallet.total_earned).where(
                    UserWallet.owner_telegram_id == owner_id
                )
            )
        ).scalar_one_or_none()
        if own_earned is not None:
            higher = (
                await session.execute(
                    select(func.count(UserWallet.id)).where(
                        UserWallet.total_earned > own_earned
                    )
                )
            ).scalar_one()
            my_rank = higher + 1

    return {"entries": entries, "my_rank": my_rank}


# ── Pets ──────────────────────────────────────────────────────────────────────

@router.post("/app/api/pet/list")
async def miniapp_pet_list(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Return user's pets (alive + up to 3 dead) and available chats to adopt."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])
    repo = PetRepository(session)
    pets, available_chats = await repo.get_pets(owner_id)
    await session.commit()  # persist any death updates flushed by get_pets
    return {"pets": pets, "available_chats": available_chats, "feed_cost": FEED_COST}


@router.post("/app/api/pet/adopt")
async def miniapp_pet_adopt(
    payload: PetAdoptRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Adopt a new pet for a chat with an active streak."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])
    pet_name = (payload.pet_name or "").strip()[:30]

    repo = PetRepository(session)
    try:
        pet = await repo.adopt(owner_id, payload.chat_id, payload.species, pet_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    return {"ok": True, "pet": pet}


@router.post("/app/api/pet/feed")
async def miniapp_pet_feed(
    payload: PetFeedRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Feed a pet, deducting FEED_COST coins."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])
    repo = PetRepository(session)
    try:
        result = await repo.feed(owner_id, payload.pet_id)
    except ValueError as exc:
        code = str(exc)
        status = 400
        if code == "already_fed":
            status = 409
        raise HTTPException(status_code=status, detail=code) from exc

    await session.commit()
    return {"ok": True, **result}


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
                "wallet_balance": u.wallet_balance,
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


@router.post("/app/api/admin/dashboard_stats")
async def admin_dashboard_stats(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Global aggregate stats for the admin analytics tab."""
    _require_admin(payload.init_data)

    stats_service = StatsService(session)
    stats = await stats_service.get_dashboard_stats()
    return {
        "total_messages": stats.total_messages,
        "total_users": stats.total_users,
        "edited_messages": stats.edited_messages,
        "deleted_messages": stats.deleted_messages,
        "media_messages": stats.media_messages,
        "text_messages": stats.text_messages,
        "media_breakdown": [
            {"media_type": item.media_type, "count": item.count}
            for item in stats.media_breakdown
        ],
    }


@router.post("/app/api/admin/wallet/set")
async def admin_wallet_set(
    payload: AdminWalletSetRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Admin: directly set a user's coin balance."""
    admin_user = _require_admin(payload.init_data)
    repo = WalletRepository(session)
    new_balance = await repo.admin_set_balance(payload.owner_telegram_id, payload.new_balance)
    await session.commit()
    logger.info(
        "Admin %s set wallet balance for user_id=%s → %s",
        admin_user.get("username", "?"),
        payload.owner_telegram_id,
        new_balance,
    )
    return {"ok": True, "new_balance": new_balance}


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


@router.post("/app/api/admin/infographic")
async def admin_infographic(
    payload: StatsRequest,
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """Generate and return the admin overview infographic as a PNG image."""
    _require_admin(payload.init_data)

    svc    = StatsService(session)
    overview  = await svc.get_admin_overview()
    dash      = await svc.get_dashboard_stats()
    growth_raw = await svc.get_admin_growth(days=30)

    # Build 30-day daily series aligned to calendar days
    now   = dt.datetime.now(dt.UTC)
    days  = [(now - dt.timedelta(days=i)).date() for i in range(29, -1, -1)]
    msgs_by_day  = growth_raw.get("messages_by_day", {})
    conns_by_day = growth_raw.get("connections_by_day", {})
    growth: list[tuple[str, int, int]] = [
        (
            d.strftime("%d.%m"),
            int(msgs_by_day.get(str(d), 0)),
            int(conns_by_day.get(str(d), 0)),
        )
        for d in days
    ]

    active_users  = sum(1 for u in overview.users if u.is_enabled and not u.is_blocked)
    blocked_users = sum(1 for u in overview.users if u.is_blocked)
    total_coins   = sum(u.wallet_balance for u in overview.users)
    avg_messages  = (
        dash.total_messages / max(1, overview.total_users)
    )
    media_pct = round(dash.media_messages / max(1, dash.total_messages) * 100)

    top_users: list[tuple[str, int, int, int]] = []
    for u in overview.users[:5]:
        parts = [p for p in (u.first_name, u.last_name) if p]
        name  = " ".join(parts) if parts else (f"@{u.username}" if u.username else str(u.owner_telegram_id))
        top_users.append((name, u.total_messages, u.total_chats, u.wallet_balance))

    stats = AdminStats(
        generated_at=now,
        total_users=overview.total_users,
        active_users=active_users,
        blocked_users=blocked_users,
        total_messages=dash.total_messages,
        avg_messages=avg_messages,
        total_coins=total_coins,
        media_pct=media_pct,
        growth=growth,
        top_users=top_users,
    )

    buf = render_admin_image(stats)
    filename = f"bot_stats_{now.strftime('%Y%m%d_%H%M')}.png"
    return StreamingResponse(
        buf,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
