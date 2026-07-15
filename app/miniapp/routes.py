"""Routes for the personal Telegram Mini App (no admin login required)."""

from __future__ import annotations

import collections as _collections
import datetime as dt
import re as _re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.dispatcher import get_bot
from app.config import get_settings
from app.database.session import get_db_session
from app.logging_config import get_logger
from app.miniapp.auth import verify_init_data
from app.models.admin_action_log import AdminActionLog
from app.models.business_connection import BusinessConnection
from app.models.message import MediaType, Message
from app.repositories.message_repository import MessageFilters, MessageRepository
from app.repositories.pet_repository import FEED_COST, RENAME_COST, SPECIES as PET_SPECIES_CATALOGUE, PetRepository
from app.repositories.shop_repository import (
    ShopRepository,
    BOOST_DOUBLE_XP_COST, BOOST_DOUBLE_XP_HOURS,
    PIN_CHAT_COST, THEME_COST, FRAME_COST, GIFT_COST, GIFT_AMOUNT,
    VALID_THEMES, VALID_FRAMES,
)
from app.repositories.quest_repository import QUESTS, QuestRepository
from app.repositories.referral_repository import ReferralRepository
from app.repositories.subscription_repository import SubscriptionRepository
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


# ── Bot username cache (avoid get_me() on every referral request) ─────────────
_bot_username_cache: str | None = None

async def _get_cached_bot_username(settings) -> str:
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache
    try:
        bot = get_bot(settings)
        if bot:
            me = await bot.get_me()
            _bot_username_cache = me.username or ""
    except Exception:
        pass
    return _bot_username_cache or ""


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
    bet: int = 10


class FlipRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    bet: int
    choice: str  # "heads" or "tails"


class MinesStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data:   str = Field(alias="initData")
    bet:         int
    mines_count: int = Field(alias="minesCount", ge=3, le=15)


class MinesRevealRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data:  str = Field(alias="initData")
    cell_index: int = Field(alias="cellIndex", ge=0, lt=25)


class MinesCashoutRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")


class CrashStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    bet:       int


class CrashCashoutRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data:  str   = Field(alias="initData")
    multiplier: float


class QuestClaimRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    quest_id: str = Field(alias="questId")


class AdminWalletSetRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    owner_telegram_id: int = Field(alias="ownerTelegramId")
    new_balance: int = Field(alias="newBalance", ge=0, le=10_000_000)


class AdminWalletAdjustRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    owner_telegram_id: int = Field(alias="ownerTelegramId")
    delta: int = Field(alias="delta", ge=-10_000_000, le=10_000_000)


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
    food_type: str = Field(alias="foodType", default="kibble")


class PetUpgradeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    pet_id: int = Field(alias="petId")
    skill: str


class PetPlayRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    pet_id: int = Field(alias="petId")


class PetCuddleRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    pet_id: int = Field(alias="petId")


class PetRenameRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    pet_id: int = Field(alias="petId")
    new_name: str = Field(alias="newName", max_length=30)


class SubscriptionStatusRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")


class SubscriptionInvoiceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")


class AdminSubUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    is_enabled: bool | None = Field(default=None, alias="isEnabled")
    price_stars: int | None = Field(default=None, alias="priceStars", ge=1, le=10000)
    duration_days: int | None = Field(default=None, alias="durationDays", ge=1, le=365)
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    benefits: dict | None = Field(default=None)


class AdminSubSubscribersRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    page: int = Field(default=1, ge=1)


class AdminSubGrantRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    owner_telegram_id: int = Field(alias="ownerTelegramId")
    duration_days: int = Field(alias="durationDays", ge=1, le=365, default=30)


class AdminSubRevokeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    owner_telegram_id: int = Field(alias="ownerTelegramId")


def _sub_status_dict(sub, config) -> dict:
    """Serialise subscription status for API responses."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    return {
        "is_enabled":    config.is_enabled,
        "price_stars":   config.price_stars,
        "duration_days": config.duration_days,
        "title":         config.title,
        "description":   config.description,
        "benefits":      config.benefits or {},
        "is_active":     sub is not None and sub.expires_at > now if sub else False,
        "expires_at":    sub.expires_at.isoformat() if sub else None,
        "days_left":     max(0, (sub.expires_at - now).days) if sub else 0,
    }


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


@router.get("/terms", response_model=None)
async def terms_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "terms.html", {})


@router.get("/privacy", response_model=None)
async def privacy_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "privacy.html", {})


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

    _sub_repo   = SubscriptionRepository(session)
    _sub_config = await _sub_repo.get_config()
    _active_sub = await _sub_repo.get_active_subscription(owner_telegram_id)
    is_premium  = _active_sub is not None and _sub_config.is_enabled

    # ── Referral activation trigger ───────────────────────────────────────────
    # If the user has a pending referral and now has a business connection, activate.
    if connection_ids:
        try:
            _ref_repo = ReferralRepository(session)
            _ref, _ref_rewards = await _ref_repo.try_activate(
                owner_telegram_id, has_business_connection=True
            )
            if _ref_rewards:
                await session.commit()
                # Notify both sides via bot (best-effort)
                try:
                    _settings = get_settings()
                    _bot = get_bot(_settings)
                    _cfg = await _ref_repo.get_config()
                    _active = await _ref_repo._count_active(_ref.referrer_telegram_id)

                    # Build referred user display name
                    _who = _ref.referred_first_name or ""
                    if _ref.referred_username:
                        _who += f" (@{_ref.referred_username})"
                    _who = _who.strip() or f"#{_ref.referred_telegram_id}"

                    # Next milestone hint
                    _next_ms = next(
                        (m for m in sorted(_cfg.milestones, key=lambda x: x["count"])
                         if m["count"] > _active),
                        None,
                    )

                    # ── Notify referrer ──────────────────────────────────────
                    from app.bot import emoji as E
                    _ref_msg = (
                        f"{E.CHECK} <b>{_who}</b> подключил бота и стал активным рефералом!\n\n"
                    )
                    if _cfg.referrer_reward_days > 0:
                        _ref_msg += f"{E.STAR} +{_cfg.referrer_reward_days} дн. Premium начислено тебе\n"
                    _ref_msg += f"👥 Всего активных рефералов: <b>{_active}</b>"
                    if _next_ms:
                        _need = _next_ms["count"] - _active
                        _ref_msg += (
                            f"\n\n{E.TARGET} До награды «{_next_ms['label']}» — ещё <b>{_need}</b>"
                        )
                    await _bot.send_message(
                        _ref.referrer_telegram_id, _ref_msg, parse_mode="HTML"
                    )

                    # ── Notify referred user (welcome bonus) ─────────────────
                    if _cfg.referee_reward_days > 0:
                        await _bot.send_message(
                            owner_telegram_id,
                            f"{E.PARTY} Ты подключил бота по реферальной ссылке — "
                            f"<b>+{_cfg.referee_reward_days} дн. Premium</b> уже у тебя!",
                            parse_mode="HTML",
                        )
                except Exception:
                    pass  # Notification is best-effort
        except Exception:
            logger.debug("Referral activation check failed for %s", owner_telegram_id)

    if not connection_ids:
        return {
            "connected": False,
            "is_premium": is_premium,
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
            "is_premium": is_premium,
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

    _sub_repo2   = SubscriptionRepository(session)
    _sub_config2 = await _sub_repo2.get_config()
    _active_sub2 = await _sub_repo2.get_active_subscription(owner_telegram_id)
    is_premium2  = _active_sub2 is not None and _sub_config2.is_enabled

    if not connection_ids:
        return {"days": 90, "activity": {}, "is_premium": is_premium2}

    try:
        stats_service = StatsService(session)
        activity = await stats_service.get_owner_activity(
            connection_ids=connection_ids, days=90
        )
        return {"days": 90, "activity": activity, "is_premium": is_premium2}
    except Exception:
        logger.exception(
            "Failed to build activity for owner_telegram_id=%s", owner_telegram_id
        )
        raise HTTPException(status_code=500, detail="Failed to load activity") from None


# ── Word / emoji frequency (premium) ────────────────────────────────────────

_STOP_WORDS: frozenset[str] = frozenset({
    "и","в","не","на","я","что","он","с","а","как","это","к","но","у","из","по",
    "да","то","все","за","бы","до","же","уже","ты","мы","вы","они","так","вот",
    "быть","есть","или","про","ну","при","со","от","об","для","им","его","её",
    "их","нас","вас","мне","тебе","тоже","ещё","еще","если","когда","тут","там",
    "здесь","меня","была","был","буду","могу","надо","нет","нам","всё","очень",
    "тебя","него","неё","них","ему","ней","ним","мой","моя","моё","твой","твоя",
    "этот","эта","эти","тот","та","те","сам","сама","само","сами","всего","мне",
    "the","a","an","is","it","in","of","to","and","i","you","he","she","we","they",
    "for","on","at","by","with","as","be","was","are","this","that","have","has",
    "had","do","did","will","would","could","should","not","but","or","if","so",
    "no","my","your","his","her","our","its","me","him","us","them","more","just",
    "get","one","now","know","see","like","well","from","been","were","all","also",
    "when","where","how","who","what","which","can","may","than","then","into","there",
})

_EMOJI_RE = _re.compile(
    r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
    r'\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF'
    r'\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF'
    r'\U00002702-\U000027B0\u2600-\u2B55]+',
    flags=_re.UNICODE,
)


@router.post("/app/api/stats/words")
async def miniapp_words(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_telegram_id = int(user["id"])
    _sub_repo = SubscriptionRepository(session)
    _sub_config = await _sub_repo.get_config()
    _active_sub = await _sub_repo.get_active_subscription(owner_telegram_id)
    if not (_active_sub is not None and _sub_config.is_enabled):
        return {"locked": True}

    conn_result = await session.execute(
        select(BusinessConnection.business_connection_id).where(
            BusinessConnection.user_telegram_id == owner_telegram_id
        )
    )
    connection_ids = [row[0] for row in conn_result.all()]
    if not connection_ids:
        return {"locked": False, "top_words": [], "top_emojis": [], "total_analyzed": 0}

    since = dt.datetime.utcnow() - dt.timedelta(days=90)
    rows = (await session.execute(
        select(Message.text, Message.caption)
        .where(
            Message.business_connection_id.in_(connection_ids),
            Message.sent_at >= since,
        )
        .limit(15000)
    )).all()

    word_counts: _collections.Counter = _collections.Counter()
    emoji_counts: _collections.Counter = _collections.Counter()
    total = 0

    _URL_RE = _re.compile(r'https?://\S+|www\.\S+|t\.me/\S+|\S+\.\S+/\S*', _re.IGNORECASE)

    for text, caption in rows:
        combined = " ".join(filter(None, [text, caption]))
        if not combined.strip():
            continue
        total += 1
        clean = _URL_RE.sub(" ", combined)
        for w in _re.findall(r'\b[a-zA-Zа-яёА-ЯЁ]{3,}\b', clean.lower()):
            if w not in _STOP_WORDS:
                word_counts[w] += 1
        for e in _EMOJI_RE.findall(clean):
            emoji_counts[e] += 1

    return {
        "locked": False,
        "top_words":   [{"word": w, "count": c} for w, c in word_counts.most_common(20)],
        "top_emojis":  [{"emoji": e, "count": c} for e, c in emoji_counts.most_common(15)],
        "total_analyzed": total,
    }


# ── Daily digest (premium) ───────────────────────────────────────────────────

@router.post("/app/api/stats/digest")
async def miniapp_digest(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_telegram_id = int(user["id"])
    _sub_repo = SubscriptionRepository(session)
    _sub_config = await _sub_repo.get_config()
    _active_sub = await _sub_repo.get_active_subscription(owner_telegram_id)
    if not (_active_sub is not None and _sub_config.is_enabled):
        return {"locked": True}

    conn_result = await session.execute(
        select(BusinessConnection.business_connection_id).where(
            BusinessConnection.user_telegram_id == owner_telegram_id
        )
    )
    connection_ids = [row[0] for row in conn_result.all()]
    if not connection_ids:
        return {"locked": False, "total_today": 0}

    today_start = dt.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    today_rows = (await session.execute(
        select(Message.chat_id, Message.is_outgoing, Message.sent_at)
        .where(
            Message.business_connection_id.in_(connection_ids),
            Message.sent_at >= today_start,
        )
        .order_by(Message.sent_at)
    )).all()

    total_today = len(today_rows)
    if total_today == 0:
        return {"locked": False, "total_today": 0}

    incoming = sum(1 for m in today_rows if not m.is_outgoing)
    outgoing = sum(1 for m in today_rows if m.is_outgoing)

    chat_msg_counts: dict[int, int] = {}
    for m in today_rows:
        chat_msg_counts[m.chat_id] = chat_msg_counts.get(m.chat_id, 0) + 1
    active_chats = len(chat_msg_counts)

    hour_counts: dict[int, int] = {}
    for m in today_rows:
        h = m.sent_at.hour
        hour_counts[h] = hour_counts.get(h, 0) + 1
    peak_hour = max(hour_counts, key=lambda h: hour_counts[h]) if hour_counts else None

    # Unanswered: chats where the last message of today was incoming (not yet replied)
    last_is_outgoing: dict[int, bool] = {}
    for m in today_rows:  # ordered by sent_at — last write wins
        last_is_outgoing[m.chat_id] = bool(m.is_outgoing)
    unanswered_count = sum(1 for v in last_is_outgoing.values() if not v)

    return {
        "locked": False,
        "date": today_start.date().isoformat(),
        "total_today": total_today,
        "incoming": incoming,
        "outgoing": outgoing,
        "active_chats": active_chats,
        "peak_hour": peak_hour,
        "unanswered_count": unanswered_count,
    }


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
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning(
            "Failed to send streak reminder to chat_id=%s: %s", payload.chat_id, exc
        )
        raise HTTPException(status_code=502, detail="Failed to send reminder") from exc

    return {"sent": True, "streak_days": streak}


def _build_streak_remind_text(streak_days: int) -> str:
    """Pick a reminder message based on how long the streak already is."""
    from app.bot import emoji as E
    days = streak_days
    if days >= 100:
        return (
            f"{E.DIAMOND} {days} дней общения подряд — это легенда! "
            f"Пишу, чтобы не прерывать наш рекорд {E.CROWN}"
        )
    if days >= 30:
        return (
            f"{E.ROCKET} {days} дней подряд! Это уже марафон — "
            "не хочу останавливаться 💪"
        )
    if days >= 14:
        return (
            f"{E.FIRE}{E.FIRE} Уже {days} дней подряд общаемся! "
            "Напоминаю о себе, чтобы серия не прервалась 😄"
        )
    if days >= 7:
        return (
            f"{E.FIRE} Неделя или больше подряд — {days} дней! "
            "Держим стрик? Напиши что-нибудь 😊"
        )
    if days >= 2:
        return (
            f"👋 У нас уже {days} дня подряд! "
            f"Напоминаю о себе — держим серию? {E.FIRE}"
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

    sub_repo = SubscriptionRepository(session)
    config   = await sub_repo.get_config()
    sub      = await sub_repo.get_active_subscription(owner_id)
    return {
        "balance": wallet.balance,
        "total_earned": wallet.total_earned,
        "total_spent": wallet.total_spent,
        "can_claim_daily": can_claim,
        "seconds_until_next_claim": secs,
        "subscription": _sub_status_dict(sub, config),
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

    # Apply subscription premium benefits (server-side only)
    sub_repo           = SubscriptionRepository(session)
    config             = await sub_repo.get_config()
    sub                = await sub_repo.get_active_subscription(owner_id)
    premium_multiplier = 1.0
    premium_bonus      = 0
    if sub and config.is_enabled:
        b                  = config.benefits or {}
        premium_multiplier = float(b.get("daily_multiplier", 1.0))
        premium_bonus      = int(b.get("daily_bonus_coins", 0))

    repo = WalletRepository(session)
    try:
        result = await repo.claim_daily(
            owner_id,
            streak_days=streak_days,
            premium_multiplier=premium_multiplier,
            premium_bonus=premium_bonus,
        )
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    return {
        "earned": result.earned,
        "base": result.base,
        "streak_bonus": result.streak_bonus,
        "new_balance": result.new_balance,
        "is_premium": sub is not None,
        "premium_multiplier": result.premium_multiplier,
        "premium_bonus": result.premium_bonus,
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
        result = await repo.spin_slots(owner_id, payload.bet)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "reels": result.reels,
        "payout": result.payout,
        "net": result.net,
        "is_jackpot": result.is_jackpot,
        "new_balance": result.new_balance,
        "bet": payload.bet,
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


# ── Mines ─────────────────────────────────────────────────────────────────────

@router.post("/app/api/wallet/mines/start")
async def wallet_mines_start(
    payload: MinesStartRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    repo = WalletRepository(session)
    try:
        result = await repo.mines_start(int(user["id"]), payload.bet, payload.mines_count)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "grid_size":   result.grid_size,
        "mines_count": result.mines_count,
        "safe_count":  result.safe_count,
    }


@router.post("/app/api/wallet/mines/reveal")
async def wallet_mines_reveal(
    payload: MinesRevealRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    repo = WalletRepository(session)
    try:
        result = await repo.mines_reveal(int(user["id"]), payload.cell_index)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "is_mine":          result.is_mine,
        "revealed_indices": result.revealed_indices,
        "mines_indices":    result.mines_indices,
        "revealed_count":   result.revealed_count,
        "multiplier":       result.multiplier,
        "potential_payout": result.potential_payout,
        "new_balance":      result.new_balance,
    }


@router.post("/app/api/wallet/mines/cashout")
async def wallet_mines_cashout(
    payload: MinesCashoutRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    repo = WalletRepository(session)
    try:
        result = await repo.mines_cashout(int(user["id"]))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "payout":        result.payout,
        "multiplier":    result.multiplier,
        "revealed_count": result.revealed_count,
        "new_balance":   result.new_balance,
    }


# ── Crash ─────────────────────────────────────────────────────────────────────

@router.post("/app/api/wallet/crash/start")
async def wallet_crash_start(
    payload: CrashStartRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    repo = WalletRepository(session)
    try:
        result = await repo.crash_start(int(user["id"]), payload.bet)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": result.ok, "new_balance": result.new_balance, "crash_at": result.crash_at}


@router.post("/app/api/wallet/crash/cashout")
async def wallet_crash_cashout(
    payload: CrashCashoutRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    repo = WalletRepository(session)
    try:
        result = await repo.crash_cashout(int(user["id"]), payload.multiplier)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "won":        result.won,
        "crash_at":   result.crash_at,
        "multiplier": result.multiplier,
        "payout":     result.payout,
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

    # ── Notify partner (B) about the new shared pet ───────────────────────
    try:
        from app.business.dispatcher import get_bot
        from app.repositories.pet_repository import SPECIES as _SPECIES

        owner_first = user.get("first_name", "")
        owner_last  = user.get("last_name", "")
        owner_name  = (owner_first + " " + owner_last).strip() or (
            f"@{user['username']}" if user.get("username") else "Пользователь"
        )
        species_info = _SPECIES.get(pet["species"], {})
        species_label = species_info.get("label", pet["species"])
        pet_emoji = (species_info.get("stages") or ["🐾"])[-1]  # adult emoji

        action = "завёл" if pet.get("mirror_created") else "тоже завёл"
        bot = get_bot(settings)
        await bot.send_message(
            chat_id=payload.chat_id,
            text=(
                f"🐾 <b>{owner_name}</b> {action} с тобой питомца!\n\n"
                f"{pet_emoji} <b>{pet['pet_name']}</b> — {species_label}\n\n"
                f"Питомец появился в твоём приложении. "
                f"Не забывай кормить его, чтобы он не умер с голоду 🍖"
            ),
            parse_mode="HTML",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pet adopt notify failed for chat_id=%s: %s", payload.chat_id, exc)

    return {"ok": True, "pet": pet}


async def _get_pet_sub_benefits(session: AsyncSession, user_id: int) -> dict:
    """Return pet-related subscription benefits for user, or defaults.
    Also applies 2× multiplier when the user has an active double_xp shop boost."""
    sub_repo = SubscriptionRepository(session)
    config   = await sub_repo.get_config()
    sub      = await sub_repo.get_active_subscription(user_id)
    feed_free     = False
    xp_multiplier = 1.0
    if sub and config.is_enabled:
        b             = config.benefits or {}
        feed_free     = bool(b.get("pet_feed_free", False))
        xp_multiplier = float(b.get("xp_multiplier", 1.0))
    # Double XP shop boost stacks multiplicatively
    shop_repo = ShopRepository(session)
    if await shop_repo.has_double_xp(user_id):
        xp_multiplier *= 2.0
    return {"feed_free": feed_free, "xp_multiplier": xp_multiplier}


@router.post("/app/api/pet/feed")
async def miniapp_pet_feed(
    payload: PetFeedRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Feed a pet, deducting FEED_COST coins (free for Premium subscribers)."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])
    benefits = await _get_pet_sub_benefits(session, owner_id)
    repo = PetRepository(session)
    try:
        result = await repo.feed(
            owner_id, payload.pet_id,
            food_type=payload.food_type,
            feed_free=benefits["feed_free"],
            xp_multiplier=benefits["xp_multiplier"],
        )
    except ValueError as exc:
        code = str(exc)
        status = 409 if code == "already_fed" else 400
        raise HTTPException(status_code=status, detail=code) from exc

    await session.commit()
    return {"ok": True, **result}


@router.post("/app/api/pet/play")
async def miniapp_pet_play(
    payload: PetPlayRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Play with a pet (free action, cooldown-based). Boosts mood + awards XP."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])
    benefits = await _get_pet_sub_benefits(session, owner_id)
    repo = PetRepository(session)
    try:
        result = await repo.play(owner_id, payload.pet_id, xp_multiplier=benefits["xp_multiplier"])
    except ValueError as exc:
        code = str(exc)
        status = 409 if code == "play_cooldown" else 400
        raise HTTPException(status_code=status, detail=code) from exc

    await session.commit()
    return {"ok": True, **result}


@router.post("/app/api/pet/cuddle")
async def miniapp_pet_cuddle(
    payload: PetCuddleRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Cuddle a pet (free, 1 h cooldown). Boosts mood + awards XP."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])
    benefits = await _get_pet_sub_benefits(session, owner_id)
    repo = PetRepository(session)
    try:
        result = await repo.cuddle(owner_id, payload.pet_id, xp_multiplier=benefits["xp_multiplier"])
    except ValueError as exc:
        code = str(exc)
        status = 409 if code == "cuddle_cooldown" else 400
        raise HTTPException(status_code=status, detail=code) from exc

    await session.commit()
    return {"ok": True, **result}


@router.post("/app/api/pet/rename")
async def miniapp_pet_rename(
    payload: PetRenameRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Rename a pet (costs RENAME_COST coins)."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])
    repo = PetRepository(session)
    try:
        result = await repo.rename(owner_id, payload.pet_id, payload.new_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    return {"ok": True, **result}


@router.post("/app/api/pet/upgrade")
async def miniapp_pet_upgrade(
    payload: PetUpgradeRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Buy a skill upgrade for a pet."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])
    repo = PetRepository(session)
    try:
        result = await repo.buy_upgrade(owner_id, payload.pet_id, payload.skill)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    return {"ok": True, **result}


@router.post("/app/api/pet/leaderboard")
async def miniapp_pet_leaderboard(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Return top 20 pets by XP (leaderboard)."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    repo = PetRepository(session)
    leaderboard = await repo.get_leaderboard(limit=20)
    return {"leaderboard": leaderboard}


# ── Subscription endpoints ────────────────────────────────────────────────────


@router.post("/app/api/subscription/status")
async def subscription_status(
    payload: SubscriptionStatusRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Return subscription config + caller's current status."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    owner_id = int(user["id"])
    sub_repo = SubscriptionRepository(session)
    config   = await sub_repo.get_config()
    sub      = await sub_repo.get_active_subscription(owner_id)
    return _sub_status_dict(sub, config)


@router.post("/app/api/subscription/invoice")
async def subscription_invoice(
    payload: SubscriptionInvoiceRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Send a Telegram Stars invoice to the caller's DM.

    The bot sends the invoice and the client handles the native Telegram
    payment sheet — no redirect needed.
    """
    from aiogram.types import LabeledPrice

    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    owner_id = int(user["id"])

    sub_repo = SubscriptionRepository(session)
    config   = await sub_repo.get_config()
    if not config.is_enabled:
        raise HTTPException(status_code=403, detail="subscription_disabled")

    # Check for existing active sub
    existing = await sub_repo.get_active_subscription(owner_id)
    if existing:
        raise HTTPException(status_code=409, detail="already_subscribed")

    bot = get_bot(settings)
    try:
        invoice_link = await bot.create_invoice_link(
            title=config.title,
            description=config.description,
            payload=f"subscription_{owner_id}",
            provider_token="",          # empty string = Telegram Stars (XTR)
            currency="XTR",
            prices=[LabeledPrice(label=config.title, amount=config.price_stars)],
        )
    except Exception as exc:
        logger.exception("Failed to create invoice link for user %s", owner_id)
        raise HTTPException(status_code=502, detail="invoice_send_failed") from exc

    return {"ok": True, "price_stars": config.price_stars, "invoice_link": invoice_link}


# ── Admin subscription endpoints ──────────────────────────────────────────────


@router.post("/app/api/admin/subscription/config")
async def admin_subscription_config(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Get the current subscription config."""
    _require_admin(payload.init_data)
    sub_repo = SubscriptionRepository(session)
    config   = await sub_repo.get_config()
    return {
        "is_enabled":    config.is_enabled,
        "price_stars":   config.price_stars,
        "duration_days": config.duration_days,
        "title":         config.title,
        "description":   config.description,
        "benefits":      config.benefits or {},
        "updated_at":    config.updated_at.isoformat() if config.updated_at else None,
    }


@router.post("/app/api/admin/subscription/update")
async def admin_subscription_update(
    payload: AdminSubUpdateRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Update subscription config (partial update — only provided fields change)."""
    _require_admin(payload.init_data)
    fields: dict = {}
    if payload.is_enabled is not None:
        fields["is_enabled"] = payload.is_enabled
    if payload.price_stars is not None:
        fields["price_stars"] = payload.price_stars
    if payload.duration_days is not None:
        fields["duration_days"] = payload.duration_days
    if payload.title is not None:
        fields["title"] = payload.title.strip()
    if payload.description is not None:
        fields["description"] = payload.description.strip()
    if payload.benefits is not None:
        # Validate keys against allowed set
        allowed_keys = {"daily_multiplier", "daily_bonus_coins", "pet_feed_free", "xp_multiplier", "max_pets_bonus"}
        clean = {k: v for k, v in payload.benefits.items() if k in allowed_keys}
        fields["benefits"] = clean

    sub_repo = SubscriptionRepository(session)
    config   = await sub_repo.update_config(**fields)
    await session.commit()
    return {
        "ok": True,
        "is_enabled":    config.is_enabled,
        "price_stars":   config.price_stars,
        "duration_days": config.duration_days,
        "title":         config.title,
        "description":   config.description,
        "benefits":      config.benefits or {},
    }


@router.post("/app/api/admin/subscription/subscribers")
async def admin_subscription_subscribers(
    payload: AdminSubSubscribersRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """List active subscribers (paginated)."""
    _require_admin(payload.init_data)
    sub_repo = SubscriptionRepository(session)
    return await sub_repo.list_subscribers(page=payload.page)


@router.post("/app/api/admin/subscription/grant")
async def admin_subscription_grant(
    payload: AdminSubGrantRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Manually grant a subscription to a user."""
    _require_admin(payload.init_data)
    sub_repo = SubscriptionRepository(session)
    sub = await sub_repo.grant(payload.owner_telegram_id, payload.duration_days)
    await session.commit()
    return {
        "ok": True,
        "expires_at": sub.expires_at.isoformat(),
        "days_left": payload.duration_days,
    }


@router.post("/app/api/admin/subscription/revoke")
async def admin_subscription_revoke(
    payload: AdminSubRevokeRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Revoke a user's active subscription."""
    _require_admin(payload.init_data)
    sub_repo = SubscriptionRepository(session)
    await sub_repo.revoke(payload.owner_telegram_id)
    await session.commit()
    return {"ok": True}


# ── Shop admin ────────────────────────────────────────────────────────────────

class AdminShopConfigRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")


class AdminShopUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    items: dict


@router.post("/app/api/admin/shop/config")
async def admin_shop_config(
    payload: AdminShopConfigRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Get full shop config for admin."""
    _require_admin(payload.init_data)
    shop_repo = ShopRepository(session)
    return {"ok": True, "items": await shop_repo.get_shop_config_admin()}


@router.post("/app/api/admin/shop/update")
async def admin_shop_update(
    payload: AdminShopUpdateRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Admin: overwrite shop config items dict."""
    _require_admin(payload.init_data)
    if not isinstance(payload.items, dict):
        raise HTTPException(status_code=422, detail="items must be a dict")
    shop_repo = ShopRepository(session)
    new_cfg = await shop_repo.update_shop_config(payload.items)
    await session.commit()
    return {"ok": True, "items": new_cfg}


@router.post("/app/api/admin/overview")
async def admin_overview(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    _require_admin(payload.init_data)

    stats_service = StatsService(session)
    overview = await stats_service.get_admin_overview()

    user_ids = [u.owner_telegram_id for u in overview.users]
    now = dt.datetime.now(dt.timezone.utc)

    # ── Bulk: active subscriptions ────────────────────────────────────────────
    from app.models.subscription import UserSubscription
    sub_rows = (await session.execute(
        select(UserSubscription.user_telegram_id, UserSubscription.expires_at)
        .where(
            UserSubscription.user_telegram_id.in_(user_ids),
            UserSubscription.is_active == True,  # noqa: E712
            UserSubscription.expires_at > now,
        )
    )).all()
    sub_map: dict[int, str] = {row[0]: row[1].isoformat() for row in sub_rows}

    # ── Bulk: referrals sent by each user ─────────────────────────────────────
    from app.models.referral import Referral
    ref_rows = (await session.execute(
        select(
            Referral.referrer_telegram_id,
            Referral.referred_telegram_id,
            Referral.referred_first_name,
            Referral.referred_username,
            Referral.status,
        )
        .where(
            Referral.referrer_telegram_id.in_(user_ids),
            Referral.status != "fraud",
        )
        .order_by(Referral.created_at.desc())
    )).all()

    ref_map: dict[int, list[dict]] = {}
    for row in ref_rows:
        ref_map.setdefault(row[0], []).append({
            "referred_telegram_id": row[1],
            "referred_first_name": row[2],
            "referred_username": row[3],
            "status": row[4],
        })

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
                "subscription_expires_at": sub_map.get(u.owner_telegram_id),
                "referrals": ref_map.get(u.owner_telegram_id, []),
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
    session.add(AdminActionLog(
        admin_username=admin_user.get("username", "?"),
        action="wallet_set",
        target_owner_telegram_id=payload.owner_telegram_id,
        details=f"balance → {new_balance}",
    ))
    await session.commit()
    logger.info(
        "Admin %s set wallet balance for user_id=%s → %s",
        admin_user.get("username", "?"),
        payload.owner_telegram_id,
        new_balance,
    )
    try:
        from app.business.dispatcher import get_bot
        bot = get_bot(get_settings())
        from app.bot import emoji as E
        await bot.send_message(
            chat_id=payload.owner_telegram_id,
            text=(
                f"{E.MONEY_BAG} Ваш баланс изменён администратором.\n"
                f"Новый баланс: <b>{new_balance:,} {E.COIN}</b>"
            ).replace(",", "\u202f"),
            parse_mode="HTML",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wallet-set notify failed for user_id=%s: %s", payload.owner_telegram_id, exc)
    return {"ok": True, "new_balance": new_balance}


@router.post("/app/api/admin/wallet/adjust")
async def admin_wallet_adjust(
    payload: AdminWalletAdjustRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Admin: add or subtract coins from a user's balance."""
    admin_user = _require_admin(payload.init_data)
    repo = WalletRepository(session)
    new_balance = await repo.admin_adjust_balance(payload.owner_telegram_id, payload.delta)
    sign = "+" if payload.delta >= 0 else ""
    session.add(AdminActionLog(
        admin_username=admin_user.get("username", "?"),
        action="wallet_adjust",
        target_owner_telegram_id=payload.owner_telegram_id,
        details=f"delta {sign}{payload.delta} → balance {new_balance}",
    ))
    await session.commit()
    logger.info(
        "Admin %s adjusted wallet for user_id=%s delta=%s%s → %s",
        admin_user.get("username", "?"),
        payload.owner_telegram_id,
        sign, payload.delta,
        new_balance,
    )
    try:
        from app.business.dispatcher import get_bot
        bot = get_bot(get_settings())
        from app.bot import emoji as E
        if payload.delta >= 0:
            delta_line = f"Начислено: <b>+{payload.delta:,} {E.COIN}</b>".replace(",", "\u202f")
        else:
            delta_line = f"Списано: <b>{payload.delta:,} {E.COIN}</b>".replace(",", "\u202f")
        await bot.send_message(
            chat_id=payload.owner_telegram_id,
            text=(
                f"{E.MONEY_BAG} Ваш баланс изменён администратором.\n"
                f"{delta_line}\n"
                f"Новый баланс: <b>{new_balance:,} {E.COIN}</b>"
            ).replace(",", "\u202f"),
            parse_mode="HTML",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Wallet-adjust notify failed for user_id=%s: %s", payload.owner_telegram_id, exc)
    return {"ok": True, "new_balance": new_balance, "delta": payload.delta}


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


# ── Shop endpoints ─────────────────────────────────────────────────────────────


class ShopStatusRequest(BaseModel):
    initData: str = Field(alias="initData", default="")
    init_data: str = ""

    @property
    def resolved_init(self) -> str:
        return self.initData or self.init_data

    model_config = {"populate_by_name": True}


class ShopBoostRequest(BaseModel):
    initData: str = Field(alias="initData", default="")
    init_data: str = ""
    boostType: str = Field(alias="boostType", default="double_xp")

    @property
    def resolved_init(self) -> str:
        return self.initData or self.init_data

    model_config = {"populate_by_name": True}


class ShopThemeRequest(BaseModel):
    initData: str = Field(alias="initData", default="")
    init_data: str = ""
    theme: str

    @property
    def resolved_init(self) -> str:
        return self.initData or self.init_data

    model_config = {"populate_by_name": True}


class ShopThemeActivateRequest(BaseModel):
    initData: str = Field(alias="initData", default="")
    init_data: str = ""
    theme: str

    @property
    def resolved_init(self) -> str:
        return self.initData or self.init_data

    model_config = {"populate_by_name": True}


class ShopFrameRequest(BaseModel):
    initData: str = Field(alias="initData", default="")
    init_data: str = ""
    frame: str

    @property
    def resolved_init(self) -> str:
        return self.initData or self.init_data

    model_config = {"populate_by_name": True}


class ShopPinChatRequest(BaseModel):
    initData: str = Field(alias="initData", default="")
    init_data: str = ""
    chatId: int | None = Field(alias="chatId", default=None)

    @property
    def resolved_init(self) -> str:
        return self.initData or self.init_data

    model_config = {"populate_by_name": True}


class ShopGiftRequest(BaseModel):
    initData: str = Field(alias="initData", default="")
    init_data: str = ""
    chatId: int = Field(alias="chatId")

    @property
    def resolved_init(self) -> str:
        return self.initData or self.init_data

    model_config = {"populate_by_name": True}


def _shop_auth(init_data: str, settings) -> int:
    user = verify_init_data(init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    return int(user["id"])


@router.post("/app/api/shop/status")
async def shop_status(
    payload: ShopStatusRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Return active boosts + settings + price list for the shop tab."""
    settings = get_settings()
    owner_id = _shop_auth(payload.resolved_init, settings)
    repo = ShopRepository(session)
    return await repo.get_shop_status(owner_id)


@router.post("/app/api/shop/boost")
async def shop_buy_boost(
    payload: ShopBoostRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Buy a timed boost (currently only double_xp)."""
    settings = get_settings()
    owner_id = _shop_auth(payload.resolved_init, settings)
    if payload.boostType != "double_xp":
        raise HTTPException(status_code=400, detail="unknown_boost_type")
    repo = ShopRepository(session)
    try:
        result = await repo.buy_double_xp(owner_id)
    except ValueError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    await session.commit()
    return {"ok": True, **result}


@router.post("/app/api/shop/theme")
async def shop_buy_theme(
    payload: ShopThemeRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Purchase and apply a UI theme."""
    settings = get_settings()
    owner_id = _shop_auth(payload.resolved_init, settings)
    repo = ShopRepository(session)
    try:
        result = await repo.buy_theme(owner_id, payload.theme)
    except ValueError as exc:
        code = str(exc)
        status = 402 if code == "insufficient_coins" else 400
        raise HTTPException(status_code=status, detail=code) from exc
    await session.commit()
    return {"ok": True, **result}


@router.post("/app/api/shop/theme/activate")
async def shop_activate_theme(
    payload: ShopThemeActivateRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Activate an already-owned theme for free."""
    settings = get_settings()
    owner_id = _shop_auth(payload.resolved_init, settings)
    repo = ShopRepository(session)
    try:
        result = await repo.activate_theme(owner_id, payload.theme)
    except ValueError as exc:
        code = str(exc)
        status = 403 if code == "not_owned" else 400
        raise HTTPException(status_code=status, detail=code) from exc
    await session.commit()
    return {"ok": True, **result}


@router.post("/app/api/shop/frame")
async def shop_buy_frame(
    payload: ShopFrameRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Purchase and apply a profile frame."""
    settings = get_settings()
    owner_id = _shop_auth(payload.resolved_init, settings)
    repo = ShopRepository(session)
    try:
        result = await repo.buy_frame(owner_id, payload.frame)
    except ValueError as exc:
        code = str(exc)
        status = 402 if code == "insufficient_coins" else 400
        raise HTTPException(status_code=status, detail=code) from exc
    await session.commit()
    return {"ok": True, **result}


@router.post("/app/api/shop/pin-chat")
async def shop_pin_chat(
    payload: ShopPinChatRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Pin (or unpin) a chat. Costs PIN_CHAT_COST coins when setting/changing."""
    settings = get_settings()
    owner_id = _shop_auth(payload.resolved_init, settings)
    repo = ShopRepository(session)
    try:
        result = await repo.pin_chat(owner_id, payload.chatId)
    except ValueError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    await session.commit()
    return {"ok": True, **result}


@router.post("/app/api/shop/gift")
async def shop_gift_coins(
    payload: ShopGiftRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Gift coins to another user (chat partner)."""
    settings = get_settings()
    owner_id = _shop_auth(payload.resolved_init, settings)
    repo = ShopRepository(session)
    try:
        result = await repo.gift_coins(owner_id, payload.chatId)
    except ValueError as exc:
        code = str(exc)
        status = 400 if code == "cannot_gift_self" else 402
        raise HTTPException(status_code=status, detail=code) from exc
    await session.commit()
    return {"ok": True, **result}


# ══════════════════════════════════════════════════════════════════════════════
#  REFERRAL SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

class ReferralInfoRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")


class AdminReferralListRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    page: int = Field(default=1)
    status: str | None = Field(default=None)
    search_id: int | None = Field(default=None, alias="searchId")


class AdminReferralConfigRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")


class AdminReferralConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    is_enabled: bool | None = Field(default=None, alias="isEnabled")
    referrer_reward_days: int | None = Field(default=None, alias="referrerRewardDays")
    referee_reward_days: int | None = Field(default=None, alias="refereeRewardDays")
    min_account_age_days: int | None = Field(default=None, alias="minAccountAgeDays")
    max_referrals_per_day: int | None = Field(default=None, alias="maxReferralsPerDay")
    milestones: list | None = Field(default=None)
    levels: list | None = Field(default=None)


class AdminReferralAdjustRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    referral_id: int = Field(alias="referralId")
    status: str  # "active" | "pending" | "fraud"
    reason: str = Field(default="")


class AdminReferralGrantRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    user_telegram_id: int = Field(alias="userTelegramId")
    reward_type: str = Field(default="premium_days", alias="rewardType")
    reward_value: str = Field(default="7", alias="rewardValue")
    label: str = Field(default="")


@router.post("/app/api/referral/info")
async def referral_info(
    payload: ReferralInfoRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Return referral stats and link for the current user."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")
    owner_id = int(user["id"])

    bot_username = await _get_cached_bot_username(settings)

    repo = ReferralRepository(session)
    stats = await repo.get_user_stats(owner_id, bot_username)
    return stats


@router.post("/app/api/admin/referral/stats")
async def admin_referral_stats(
    payload: AdminReferralConfigRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    _require_admin(payload.init_data)
    repo = ReferralRepository(session)
    return await repo.admin_stats()


@router.post("/app/api/admin/referral/list")
async def admin_referral_list(
    payload: AdminReferralListRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    _require_admin(payload.init_data)
    repo = ReferralRepository(session)
    return await repo.admin_list(
        page=payload.page,
        status_filter=payload.status,
        search_id=payload.search_id,
    )


@router.post("/app/api/admin/referral/config")
async def admin_referral_config(
    payload: AdminReferralConfigRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    _require_admin(payload.init_data)
    repo = ReferralRepository(session)
    cfg = await repo.get_config()
    return {
        "is_enabled": cfg.is_enabled,
        "referrer_reward_days": cfg.referrer_reward_days,
        "referee_reward_days": cfg.referee_reward_days,
        "min_account_age_days": cfg.min_account_age_days,
        "max_referrals_per_day": cfg.max_referrals_per_day,
        "milestones": cfg.milestones,
        "levels": cfg.levels,
    }


@router.post("/app/api/admin/referral/config/update")
async def admin_referral_config_update(
    payload: AdminReferralConfigUpdateRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    _require_admin(payload.init_data)
    repo = ReferralRepository(session)
    updates: dict = {}
    if payload.is_enabled is not None:
        updates["is_enabled"] = payload.is_enabled
    if payload.referrer_reward_days is not None:
        updates["referrer_reward_days"] = payload.referrer_reward_days
    if payload.referee_reward_days is not None:
        updates["referee_reward_days"] = payload.referee_reward_days
    if payload.min_account_age_days is not None:
        updates["min_account_age_days"] = payload.min_account_age_days
    if payload.max_referrals_per_day is not None:
        updates["max_referrals_per_day"] = payload.max_referrals_per_day
    if payload.milestones is not None:
        updates["milestones"] = payload.milestones
    if payload.levels is not None:
        updates["levels"] = payload.levels
    cfg = await repo.update_config(**updates)
    await session.commit()
    return {
        "ok": True,
        "is_enabled": cfg.is_enabled,
        "referrer_reward_days": cfg.referrer_reward_days,
        "referee_reward_days": cfg.referee_reward_days,
        "min_account_age_days": cfg.min_account_age_days,
        "max_referrals_per_day": cfg.max_referrals_per_day,
        "milestones": cfg.milestones,
        "levels": cfg.levels,
    }


@router.post("/app/api/admin/referral/adjust")
async def admin_referral_adjust(
    payload: AdminReferralAdjustRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    _require_admin(payload.init_data)
    if payload.status not in ("active", "pending", "fraud"):
        raise HTTPException(status_code=400, detail="Invalid status")
    repo = ReferralRepository(session)
    ok = await repo.admin_set_status(payload.referral_id, payload.status, payload.reason)
    if not ok:
        raise HTTPException(status_code=404, detail="Referral not found")
    await session.commit()
    return {"ok": True}


@router.post("/app/api/admin/referral/grant")
async def admin_referral_grant(
    payload: AdminReferralGrantRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    _require_admin(payload.init_data)
    repo = ReferralRepository(session)
    result = await repo.admin_grant_bonus(
        user_telegram_id=payload.user_telegram_id,
        reward_type=payload.reward_type,
        reward_value=payload.reward_value,
        label=payload.label,
    )
    await session.commit()
    return result
