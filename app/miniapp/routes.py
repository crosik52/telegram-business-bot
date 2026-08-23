"""Routes for the personal Telegram Mini App (no admin login required)."""

from __future__ import annotations

import asyncio
import collections as _collections
import datetime as dt
import hashlib
import re as _re

import time as _time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.business.dispatcher import get_bot
from app.config import get_settings
from app.database.session import get_db_session
from app.logging_config import get_logger
from app.miniapp.auth import verify_init_data
from app.models.admin_action_log import AdminActionLog
from app.models.business_connection import BusinessConnection
from app.models.message import MediaType, Message
from app.models.relationship import MARRIAGE_DAILY_BONUS
from app.repositories.message_repository import MessageFilters, MessageRepository
from app.repositories.pet_repository import FEED_COST, RENAME_COST, SPECIES as PET_SPECIES_CATALOGUE, PetRepository
from app.repositories.shop_repository import (
    ShopRepository,
    BOOST_DOUBLE_XP_COST, BOOST_DOUBLE_XP_HOURS,
    PIN_CHAT_COST, THEME_COST, FRAME_COST, GIFT_COST, GIFT_AMOUNT,
    VALID_THEMES, VALID_FRAMES, COIN_PACKAGES,
)
from app.repositories.quest_repository import QUESTS, QuestRepository
from app.repositories.giveaway_repository import GiveawayRepository
from app.repositories.referral_repository import ReferralRepository
from app.repositories.relationship_repository import RelationshipRepository
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


class AdminDbCleanupRequest(BaseModel):
    model_config = {"populate_by_name": True}
    init_data: str = Field(alias="initData")
    keep_days: int = Field(default=30, alias="keepDays")


class AdminInitDataOnlyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")


class AdminMessagesRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    owner_telegram_id: int = Field(alias="ownerTelegramId")
    chat_id: int = Field(alias="chatId")
    page: int = Field(default=1)
    text_query: str | None = Field(alias="textQuery", default=None)


class AdminSearchChatsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    query: str


class AdminBroadcastRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    text: str
    # [[{text, url}, ...], ...] — rows of inline keyboard buttons
    buttons: list[list[dict]] | None = None


class AdminActionLogRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    page: int = Field(default=1)


# ── Avatar URL cache (file_id → CDN URL, ~55-min TTL) ────────────────────────
# Structure: user_telegram_id -> (cdn_url, expires_ts)
_avatar_url_cache: dict[int, tuple[str, float]] = {}


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


# ── Subscription gate: short-lived per-user cache ────────────────────────────
# Maps user_id → (timestamp, is_subscribed).  Cleared automatically after TTL.
_sub_cache: dict[int, tuple[float, bool]] = {}
_SUB_CACHE_TTL = 60.0  # seconds


async def _is_subscribed(user_id: int, session: AsyncSession) -> bool:
    """Return True if *user_id* passes the channel gate (or no gate is active).

    Results are cached for _SUB_CACHE_TTL seconds to avoid a Telegram API call
    on every mini-app action.  Fails open on transient errors.
    """
    now = _time.monotonic()
    cached = _sub_cache.get(user_id)
    if cached and (now - cached[0]) < _SUB_CACHE_TTL:
        return cached[1]

    try:
        from app.repositories.channel_repository import ChannelRepository as _CR  # noqa: PLC0415
        from app.services.channel_subscription_service import get_unsubscribed_channels as _guc  # noqa: PLC0415
        active = await _CR(session).get_active()
        if not active:
            _sub_cache[user_id] = (now, True)
            return True
        _bot = get_bot(get_settings())
        if not _bot:
            _sub_cache[user_id] = (now, True)
            return True
        unsub = await _guc(_bot, user_id, active)
        ok = len(unsub) == 0
        _sub_cache[user_id] = (now, ok)
        return ok
    except Exception:
        return True  # fail open on transient errors


async def _assert_subscribed(user_id: int, session: AsyncSession) -> None:
    """Raise HTTP 403 if *user_id* hasn't subscribed to all required channels."""
    if not await _is_subscribed(user_id, session):
        raise HTTPException(
            status_code=403,
            detail={"subscription_gate": True,
                    "message": "Subscribe to required channels to use this feature"},
        )


@router.get("/app/api/avatar/{user_id}")
async def get_avatar(user_id: int, session: AsyncSession = Depends(get_db_session)):
    """Proxy Telegram profile photo for a user. Returns 404 when no photo stored."""
    from app.models.user import TelegramUser as _TU

    cached = _avatar_url_cache.get(user_id)
    if cached and cached[1] > _time.monotonic():
        return RedirectResponse(url=cached[0], status_code=302)

    user_row = (
        await session.execute(select(_TU).where(_TU.telegram_user_id == user_id))
    ).scalar_one_or_none()

    if not user_row or not user_row.photo_file_id:
        raise HTTPException(status_code=404, detail="No avatar")

    try:
        settings = get_settings()
        bot = get_bot(settings)
        if bot is None:
            raise HTTPException(status_code=503, detail="Bot unavailable")
        tg_file = await bot.get_file(user_row.photo_file_id)
        cdn_url = (
            f"https://api.telegram.org/file/bot{settings.telegram_bot_token}"
            f"/{tg_file.file_path}"
        )
        _avatar_url_cache[user_id] = (cdn_url, _time.monotonic() + 3300)  # 55 min
        return RedirectResponse(url=cdn_url, status_code=302)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail="Avatar unavailable")


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


class WalletRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")


class ClaimDailyRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")


class SlotSpinRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    bet: int = Field(default=10, ge=10, le=5000)


class FlipRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data: str = Field(alias="initData")
    bet: int = Field(ge=1, le=5000)
    choice: str  # "heads" or "tails"


class MinesStartRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data:   str = Field(alias="initData")
    bet:         int = Field(ge=1, le=5000)
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
    bet:       int = Field(ge=1, le=5000)


class CrashCashoutRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data:  str   = Field(alias="initData")
    multiplier: float = Field(ge=1.0, le=200.0)


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
    skill: str = Field(min_length=1, max_length=30, pattern=r"^[a-z_]+$")


class RelPartnerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data:    str = Field(alias="initData")
    partner_id:   int = Field(alias="partnerId")
    gift_id:      str | None = Field(default=None, alias="giftId")
    quest_id:     str | None = Field(default=None, alias="questId")
    # Category for new requests: "friendship" | "romantic" (default)
    category:     str = Field(default="romantic")
    # For change-category endpoint
    new_category: str | None = Field(default=None, alias="newCategory")


class RelRespondRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data:  str  = Field(alias="initData")
    partner_id: int  = Field(alias="partnerId")
    accept:     bool

class RelPostcardRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    init_data:  str = Field(alias="initData")
    partner_id: int = Field(alias="partnerId")
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
    init_data:   str = Field(alias="initData")
    plan_stars:  int | None = Field(default=None, alias="plan_stars")
    plan_days:   int | None = Field(default=None, alias="plan_days")


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
    status_filter: str | None = Field(default=None, alias="statusFilter")


class AdminSubGrantRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    owner_telegram_id: int = Field(alias="ownerTelegramId")
    duration_days: int = Field(alias="durationDays", ge=1, le=365, default=30)
    sub_type: str = Field(alias="subType", default="premium")


class AdminSubRevokeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    owner_telegram_id: int = Field(alias="ownerTelegramId")


class AdminChannelAddRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    username: str
    title: str | None = None


class AdminChannelActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    channel_id: int = Field(alias="channelId")


def _sub_status_dict(sub, config, vip_config=None) -> dict:
    """Serialise subscription status for API responses."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    is_active = bool(sub is not None and sub.expires_at > now)
    sub_type   = (getattr(sub, "sub_type", None) or "premium") if sub else None
    is_vip     = is_active and sub_type == "vip"
    # For active VIP, expose VIP benefits; else Premium
    effective_benefits = config.benefits or {}
    if is_vip and vip_config:
        effective_benefits = vip_config.benefits or {}
    return {
        "is_enabled":    config.is_enabled,
        "price_stars":   config.price_stars,
        "duration_days": config.duration_days,
        "title":         config.title,
        "description":   config.description,
        "benefits":      effective_benefits,
        "is_active":     is_active,
        "is_vip":        is_vip,
        "sub_type":      sub_type,
        "expires_at":    sub.expires_at.isoformat() if sub else None,
        "days_left":     max(0, (sub.expires_at - now).days) if sub else 0,
        # Pass VIP config prices to frontend for rendering VIP plan cards
        "vip_config": {
            "is_enabled":    vip_config.is_enabled if vip_config else True,
            "price_stars":   vip_config.price_stars if vip_config else 210,
            "duration_days": vip_config.duration_days if vip_config else 30,
            "benefits":      vip_config.benefits if vip_config else {},
        } if vip_config else None,
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
    response = templates.TemplateResponse(request, "miniapp.html", {})
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


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

    # ── Channel subscription gate ─────────────────────────────────────────────
    try:
        from app.repositories.channel_repository import ChannelRepository as _CR  # noqa: PLC0415
        from app.services.channel_subscription_service import get_unsubscribed_channels as _guc  # noqa: PLC0415
        _active_chs = await _CR(session).get_active()
        if _active_chs:
            _bot = get_bot(get_settings())
            if _bot:
                _unsub = await _guc(_bot, owner_telegram_id, _active_chs)
                if _unsub:
                    return {
                        "subscription_gate": True,
                        "required_channels": [
                            {
                                "title": ch.display_title,
                                "username": ch.channel_username,
                                "url": ch.join_url,
                            }
                            for ch in _unsub
                        ],
                    }
    except Exception:
        logger.exception(
            "channel_gate miniapp: check failed for user %s — allowing through",
            owner_telegram_id,
        )

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
            if _ref is not None:
                # Phase 1: commit the activation (welcome + per-activation rewards).
                await session.commit()
                # Phase 2: evaluate milestones AFTER commit so that _count_active
                # reads fully committed state — including any concurrent activations
                # that committed at the same time (prevents the TOCTOU skip race).
                _ms_rewards = await _ref_repo.evaluate_and_grant_milestones(
                    _ref.referrer_telegram_id, _ref.id
                )
                if _ms_rewards:
                    await session.commit()
                    _ref_rewards.extend(_ms_rewards)
            if _ref_rewards:
                # Notify both sides via bot in background — never block the HTTP response.
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

                from app.bot import emoji as E

                # ── Notify referrer ──────────────────────────────────────────
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

                async def _send_referral_notifications(
                    bot=_bot,
                    referrer_id=_ref.referrer_telegram_id,
                    referred_id=owner_telegram_id,
                    ref_msg=_ref_msg,
                    referee_days=_cfg.referee_reward_days,
                ) -> None:
                    try:
                        await bot.send_message(referrer_id, ref_msg, parse_mode="HTML")
                    except Exception as exc:
                        logger.warning(
                            "Referral activation: failed to notify referrer %s "
                            "(referred=%s): %s",
                            referrer_id, referred_id, exc,
                        )
                    if referee_days > 0:
                        try:
                            await bot.send_message(
                                referred_id,
                                f"{E.PARTY} Ты подключил бота по реферальной ссылке — "
                                f"<b>+{referee_days} дн. Premium</b> уже у тебя!",
                                parse_mode="HTML",
                            )
                        except Exception as exc:
                            logger.warning(
                                "Referral activation: failed to notify referred user %s "
                                "(referrer=%s): %s",
                                referred_id, referrer_id, exc,
                            )

                asyncio.create_task(_send_referral_notifications())
        except Exception:
            logger.exception(
                "Referral activation check failed for user %s", owner_telegram_id
            )

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
            "top_interlocutors": await _enrich_interlocutors(
                session, owner_telegram_id, stats.top_interlocutors
            ),
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

    sub_repo   = SubscriptionRepository(session)
    config     = await sub_repo.get_config()
    vip_config = await sub_repo.get_vip_config()
    sub        = await sub_repo.get_active_subscription(owner_id)
    # Short version token derived from balance + last-mutation timestamp.
    # Changes any time a server-side grant or deduction touches the wallet row.
    _wallet_ver_raw = f"{wallet.balance}:{wallet.updated_at.isoformat() if wallet.updated_at else ''}"
    wallet_version = hashlib.sha256(_wallet_ver_raw.encode()).hexdigest()[:12]
    return {
        "balance": wallet.balance,
        "total_earned": wallet.total_earned,
        "total_spent": wallet.total_spent,
        "can_claim_daily": can_claim,
        "seconds_until_next_claim": secs,
        "subscription": _sub_status_dict(sub, config, vip_config),
        "wallet_version": wallet_version,
    }


@router.post("/app/api/wallet/version")
async def wallet_version_check(
    payload: WalletRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Lightweight endpoint: returns a short hash of the wallet's current
    balance and last-mutation timestamp.  Clients use this to detect
    server-side coin grants or deductions without fetching the full wallet."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    owner_id = int(user["id"])
    repo = WalletRepository(session)
    wallet = await repo.get_or_create(owner_id)
    raw = f"{wallet.balance}:{wallet.updated_at.isoformat() if wallet.updated_at else ''}"
    version = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return {"wallet_version": version}


@router.post("/app/api/wallet/claim_daily")
async def wallet_claim_daily(
    payload: ClaimDailyRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    owner_id = int(user["id"])
    await _assert_subscribed(owner_id, session)

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
        "marriage_bonus": result.marriage_bonus,
        "marriage_count": result.marriage_count,
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
    await _assert_subscribed(owner_id, session)
    repo = WalletRepository(session)
    try:
        result = await repo.spin_slots(owner_id, payload.bet, first_name=user.get("first_name", "Игрок"))
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
    await _assert_subscribed(owner_id, session)
    repo = WalletRepository(session)
    try:
        result = await repo.flip_coin(owner_id, payload.bet, payload.choice, first_name=user.get("first_name", "Игрок"))
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
    owner_id = int(user["id"])
    await _assert_subscribed(owner_id, session)
    repo = WalletRepository(session)
    try:
        result = await repo.mines_start(
            owner_id, payload.bet, payload.mines_count,
            first_name=user.get("first_name", "Игрок"),
        )
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
        "mines_indices": result.mines_indices,
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
    owner_id = int(user["id"])
    await _assert_subscribed(owner_id, session)
    repo = WalletRepository(session)
    try:
        result = await repo.crash_start(
            owner_id, payload.bet,
            first_name=user.get("first_name", "Игрок"),
        )
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


@router.get("/app/api/wallet/live_players")
async def wallet_live_players() -> dict:
    """Return live game activity for mines and crash. No auth required."""
    from app.repositories.wallet_repository import get_live_players  # noqa: PLC0415
    return get_live_players()


@router.get("/app/api/wallet/casino-leaderboard")
async def wallet_casino_leaderboard(
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return top-10 biggest casino wins for today and this week. No auth required."""
    from app.repositories.wallet_repository import get_casino_leaderboard  # noqa: PLC0415
    return await get_casino_leaderboard(session)


@router.get("/app/api/wallet/crash/history")
async def wallet_crash_history() -> dict:
    """Return today's last 10 crash multipliers (public, no auth required)."""
    import datetime as dt
    from app.repositories.wallet_repository import _crash_history
    today = dt.date.today()
    today_entries = [h for h in _crash_history if h["ts"].date() == today]
    recent = today_entries[-10:]
    return {"history": [round(h["crash_at"], 2) for h in recent]}


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
    from app.models.subscription import UserSubscription  # local to avoid circular dep

    # Secondary sort by owner_telegram_id guarantees a deterministic order when
    # balances are tied, so in-list ranks and the pinned-row rank below stay
    # consistent with each other.
    top_rows = (
        await session.execute(
            select(UserWallet.owner_telegram_id, UserWallet.balance, UserWallet.total_earned)
            .order_by(UserWallet.balance.desc(), UserWallet.owner_telegram_id.asc())
            .limit(15)
        )
    ).all()

    top_ids = [r[0] for r in top_rows]
    names_map: dict[int, tuple] = {}
    subs_map: dict[int, str | None] = {}
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
        sub_rows = (
            await session.execute(
                select(UserSubscription.user_telegram_id, UserSubscription.sub_type)
                .where(
                    UserSubscription.user_telegram_id.in_(top_ids),
                    UserSubscription.is_active == True,  # noqa: E712
                )
                # DISTINCT ON requires ORDER BY to start with the same column
                .order_by(
                    UserSubscription.user_telegram_id,
                    UserSubscription.sub_type.desc(),  # vip > premium alphabetically
                    UserSubscription.id.desc(),
                )
                .distinct(UserSubscription.user_telegram_id)
            )
        ).all()
        names_map = {r[0]: (r[1], r[2], r[3]) for r in name_rows}
        subs_map = {r[0]: r[1] for r in sub_rows}

    from app.models.user import TelegramUser as _TU

    photos_map: dict[int, str | None] = {}
    if top_ids:
        photo_rows = (
            await session.execute(
                select(_TU.telegram_user_id, _TU.photo_file_id).where(
                    _TU.telegram_user_id.in_(top_ids)
                )
            )
        ).all()
        photos_map = {r[0]: r[1] for r in photo_rows}

    entries = []
    my_rank: int | None = None
    own_balance: int | None = None
    for i, row in enumerate(top_rows):
        fn, ln, un = names_map.get(row[0], (None, None, None))
        name_parts = [p for p in (fn, ln) if p]
        display = " ".join(name_parts) if name_parts else (f"@{un}" if un else "Аноним")
        is_self = row[0] == owner_id
        if is_self:
            my_rank = i + 1
            own_balance = row[1]
        entries.append(
            {
                "rank": i + 1,
                "display_name": display,
                "is_self": is_self,
                "balance": row[1],
                "total_earned": row[2],
                "sub_type": subs_map.get(row[0]),
                "avatar_url": (
                    f"/app/api/avatar/{row[0]}"
                    if photos_map.get(row[0])
                    else None
                ),
            }
        )

    if my_rank is None:
        own_balance = (
            await session.execute(
                select(UserWallet.balance).where(
                    UserWallet.owner_telegram_id == owner_id
                )
            )
        ).scalar_one_or_none()
        if own_balance is not None:
            # Count wallets that rank *before* the caller using the same ordering
            # as the top-15 query: balance DESC, then owner_telegram_id ASC.
            # A wallet ranks before ours when it has a strictly higher balance,
            # OR the same balance but a lower (earlier-sorting) telegram_id.
            higher = (
                await session.execute(
                    select(func.count(UserWallet.id)).where(
                        or_(
                            UserWallet.balance > own_balance,
                            and_(
                                UserWallet.balance == own_balance,
                                UserWallet.owner_telegram_id < owner_id,
                            ),
                        )
                    )
                )
            ).scalar_one()
            my_rank = higher + 1

    me = {"rank": my_rank, "balance": own_balance} if my_rank is not None else None
    return {"entries": entries, "my_rank": my_rank, "me": me}


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
    benefits = await _get_pet_sub_benefits(session, owner_id)
    return {
        "pets": pets,
        "available_chats": available_chats,
        "feed_cost": FEED_COST,
        "xp_multiplier": benefits["xp_multiplier"],
    }


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
    await _assert_subscribed(owner_id, session)
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

        bot = get_bot(settings)
        await bot.send_message(
            chat_id=payload.chat_id,
            text=(
                f"🐾 <b>{owner_name}</b> завёл с тобой питомца!\n\n"
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
    await _assert_subscribed(owner_id, session)
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
    await _assert_subscribed(owner_id, session)
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
    await _assert_subscribed(owner_id, session)
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
    await _assert_subscribed(owner_id, session)
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
    await _assert_subscribed(owner_id, session)
    repo = PetRepository(session)
    try:
        result = await repo.buy_upgrade(owner_id, payload.pet_id, payload.skill)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    return {"ok": True, **result}


class PetReviveInvoiceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    pet_id: int = Field(alias="petId")


class PetBattleRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    pet_id:    int = Field(alias="petId")
    wager:     int = Field(default=100, ge=50, le=5000)


class PetBattleRespondRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    pet_id:    int = Field(alias="petId")
    accept:    bool


class PetBattleStatusRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    pet_id:    int = Field(alias="petId")


_PET_REVIVE_STARS = 10


@router.post("/app/api/pet/revive/invoice")
async def miniapp_pet_revive_invoice(
    payload: PetReviveInvoiceRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Create a 10-Star invoice link to revive a dead pet.

    Validates eligibility first so the user never pays for an un-revivable pet.
    The actual revival happens in the successful_payment bot handler.
    """
    from aiogram.types import LabeledPrice

    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])

    # Validate eligibility without locking (read-only pre-check)
    from app.repositories.pet_repository import PetRepository
    repo = PetRepository(session)
    import datetime as _dt
    from app.models.pet import ChatPet as _ChatPet
    from sqlalchemy import select as _sel
    now = _dt.datetime.now(_dt.timezone.utc)
    pet = (
        await session.execute(
            _sel(_ChatPet).where(
                _ChatPet.id == payload.pet_id,
                (_ChatPet.user_a_id == owner_id) | (_ChatPet.user_b_id == owner_id),
            )
        )
    ).scalar_one_or_none()

    if pet is None:
        raise HTTPException(status_code=404, detail="pet_not_found")
    if pet.is_alive:
        raise HTTPException(status_code=409, detail="pet_already_alive")
    if pet.revival_count >= pet.max_revivals:
        raise HTTPException(status_code=409, detail="no_revivals_left")
    if pet.died_at is None or (_dt.datetime.now(_dt.timezone.utc) - (
        pet.died_at if pet.died_at.tzinfo else pet.died_at.replace(tzinfo=_dt.timezone.utc)
    )).total_seconds() > 3 * 86400:
        raise HTTPException(status_code=409, detail="revival_window_expired")

    bot = get_bot(settings)
    revivals_left = pet.max_revivals - pet.revival_count
    try:
        invoice_link = await bot.create_invoice_link(
            title=f"Возрождение питомца «{pet.pet_name}»",
            description=(
                f"Возродить питомца с сохранённым прогрессом. "
                f"Осталось возрождений: {revivals_left - 1} из {pet.max_revivals}"
            ),
            payload=f"pet_revive_{owner_id}_{payload.pet_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Возрождение питомца", amount=_PET_REVIVE_STARS)],
        )
    except Exception as exc:
        logger.exception("Failed to create pet revive invoice for user %s", owner_id)
        raise HTTPException(status_code=502, detail="invoice_send_failed") from exc

    return {"ok": True, "invoice_link": invoice_link, "stars": _PET_REVIVE_STARS}


# ── Pet battle endpoints ───────────────────────────────────────────────────────

@router.post("/app/api/pet/battle/challenge")
async def pet_battle_challenge(
    payload: PetBattleRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Issue a battle challenge to the pet's partner."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")
    owner_id = int(user["id"])
    repo = PetRepository(session)
    try:
        result = await repo.battle_challenge(owner_id, payload.pet_id, payload.wager)
        await session.commit()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/app/api/pet/battle/status")
async def pet_battle_status(
    payload: PetBattleStatusRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Return pending battle info for a pet (if any)."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")
    owner_id = int(user["id"])
    repo = PetRepository(session)
    try:
        return await repo.get_battle_status(owner_id, payload.pet_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/app/api/pet/battle/respond")
async def pet_battle_respond(
    payload: PetBattleRespondRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Accept, decline, or cancel a pending battle challenge."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")
    owner_id = int(user["id"])
    repo = PetRepository(session)
    try:
        result = await repo.battle_respond(owner_id, payload.pet_id, payload.accept)
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Notify the challenger via DM (fire-and-forget; never block the response)
    challenger_id = result.get("challenger_id")
    # Skip when the challenger cancelled their own challenge (no challenger_id returned)
    if challenger_id and challenger_id != owner_id:
        try:
            bot = get_bot(settings)
            if result.get("accepted"):
                if result.get("challenger_won"):
                    text = f"🏆 Питомец победил! +{result['wager']} 🪙"
                else:
                    text = f"😔 Питомец проиграл. −{result['wager']} 🪙"
            else:
                text = "⚔️ Партнёр отклонил вызов на бой."
            await bot.send_message(chat_id=challenger_id, text=text)
        except Exception:
            logger.warning("Failed to send battle result DM to challenger %s", challenger_id)

    return result


@router.post("/app/api/pet/leaderboard")
async def miniapp_pet_leaderboard(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Return top 20 pets by XP (leaderboard) plus the caller's own rank."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    owner_id = int(user["id"])
    repo = PetRepository(session)
    leaderboard = await repo.get_leaderboard(limit=20)
    me = await repo.get_user_rank(owner_id)
    return {"leaderboard": leaderboard, "me": me}


# ── Relationship helpers ──────────────────────────────────────────────────────


def _verify_rel_init(init_data: str, settings) -> int:
    user = verify_init_data(init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    return int(user["id"])


async def _enrich_interlocutors(
    session, owner_id: int, interlocutors
) -> list[dict]:
    """Build the top_interlocutors list annotated with relationship data."""
    try:
        _rel_repo = RelationshipRepository(session)
        _rels     = await _rel_repo.get_for_user(owner_id)
        _by_pid   = {
            (_r.user_b_id if _r.user_a_id == owner_id else _r.user_a_id):
            _rel_repo.to_dict(_r, owner_id)
            for _r in _rels
        }
    except Exception:
        _by_pid = {}

    from app.models.user import TelegramUser as _TU

    # Bulk-fetch photo_file_ids so we can expose avatar_url per contact
    chat_ids = [s.chat_id for s in interlocutors]
    _photo_map: dict[int, str | None] = {}
    if chat_ids:
        _photo_rows = (
            await session.execute(
                select(_TU.telegram_user_id, _TU.photo_file_id).where(
                    _TU.telegram_user_id.in_(chat_ids)
                )
            )
        ).all()
        _photo_map = {r[0]: r[1] for r in _photo_rows}

    return [
        {
            "chat_id":          s.chat_id,
            "display_name":     s.display_name,
            "username":         s.username,
            "message_count":    s.message_count,
            "edited_count":     s.edited_count,
            "deleted_count":    s.deleted_count,
            "last_message_at": (
                s.last_message_at.isoformat() if s.last_message_at else None
            ),
            "streak_days":      s.streak_days,
            "longest_streak":   s.longest_streak,
            "mutual_connected": s.mutual_connected,
            "relationship":     _by_pid.get(s.chat_id),
            "avatar_url": (
                f"/app/api/avatar/{s.chat_id}"
                if _photo_map.get(s.chat_id)
                else None
            ),
        }
        for s in interlocutors
    ]


# ── Relationship endpoints ────────────────────────────────────────────────────


@router.post("/app/api/relationships/list")
async def rel_list(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    repo = RelationshipRepository(session)
    rels = await repo.get_for_user(owner_id)

    # Fetch partner display names
    from app.models.user import TelegramUser as _TU
    partner_ids = [
        (r.user_b_id if r.user_a_id == owner_id else r.user_a_id) for r in rels
    ]
    name_map: dict[int, dict] = {}
    if partner_ids:
        for _u in (
            await session.execute(
                select(_TU).where(_TU.telegram_user_id.in_(partner_ids))
            )
        ).scalars():
            parts = [p for p in [_u.first_name, _u.last_name] if p]
            name_map[_u.telegram_user_id] = {
                "name":     " ".join(parts) or f"#{_u.telegram_user_id}",
                "username": _u.username,
            }

    # Anniversary auto-congratulations (credited on first open that day)
    anniversaries: list[dict] = []
    for r in rels:
        try:
            hit = await repo.process_anniversary(r)
            if hit:
                pid = r.user_b_id if r.user_a_id == owner_id else r.user_a_id
                anniversaries.append({"partner_id": pid, **hit})
        except Exception:
            logger.warning("anniversary processing failed for rel %s", r.id, exc_info=True)
    if anniversaries:
        await session.commit()

    result = []
    for r in rels:
        d = repo.to_dict(r, owner_id)
        info = name_map.get(d["partner_id"], {})
        d["partner_name"]     = info.get("name", f"#{d['partner_id']}")
        d["partner_username"] = info.get("username")
        d["quests"]           = repo.quests_for(r) if r.status == "active" else []
        result.append(d)

    from app.models.relationship import GIFT_TYPES as _GT
    return {
        "relationships": result,
        "gift_types": [{"id": gid, **g} for gid, g in _GT.items()],
        "anniversaries": anniversaries,
    }


@router.post("/app/api/relationships/leaderboard")
async def rel_leaderboard(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Return top 20 relationships by XP plus the caller's own rank."""
    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    repo = RelationshipRepository(session)
    leaderboard = await repo.get_leaderboard(limit=20)
    me = await repo.get_user_rank(owner_id)
    return {"leaderboard": leaderboard, "me": me}


@router.post("/app/api/relationships/request")
async def rel_request(
    payload: RelPartnerRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    await _assert_subscribed(owner_id, session)
    repo     = RelationshipRepository(session)
    try:
        rel = await repo.send_request(
            owner_id, payload.partner_id,
            category=payload.category if payload.category in ("friendship", "romantic") else "romantic",
        )
        await session.commit()
        # Push notification to partner + fallback owner alert
        _partner_not_reachable = False
        _bot = get_bot(settings)
        if _bot:
            try:
                from app.models.user import TelegramUser as _TU
                from app.models.message import Message as _Msg
                from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

                _me = (await session.execute(
                    select(_TU).where(_TU.telegram_user_id == owner_id)
                )).scalar_one_or_none()
                _parts = [p for p in [
                    _me.first_name if _me else None,
                    _me.last_name  if _me else None,
                ] if p]
                _name = " ".join(_parts) or f"#{owner_id}"

                _kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Принять", callback_data=f"rel_accept:{owner_id}"),
                    InlineKeyboardButton(text="❌ Отказать", callback_data=f"rel_decline:{owner_id}"),
                ]])

                # ── Lookup bc_ids ─────────────────────────────────────────
                # owner_bc_id: owner's connection → used to write into the
                #              owner↔partner conversation from the owner's side.
                # partner_bc_id: partner's connection → used to write into the
                #                partner↔owner conversation from the partner's
                #                side. This is the primary delivery channel:
                #                the message lands directly in the partner's
                #                chat with the owner, with the keyboard.
                _owner_bc_id: str | None = None
                _partner_bc_id: str | None = None
                try:
                    from app.models.message import Message as _Msg
                    # Owner's active bc_id for this chat
                    _owner_conn_ids = (await session.execute(
                        select(BusinessConnection.business_connection_id).where(
                            BusinessConnection.user_telegram_id == owner_id,
                            BusinessConnection.is_enabled.is_(True),
                        )
                    )).scalars().all()
                    if _owner_conn_ids:
                        _owner_bc_id = (await session.execute(
                            select(_Msg.business_connection_id).where(
                                _Msg.business_connection_id.in_(_owner_conn_ids),
                                _Msg.chat_id == payload.partner_id,
                            ).limit(1)
                        )).scalar_one_or_none()
                    # Partner's own active bc_id (they also connected the bot)
                    _partner_bc_id = (await session.execute(
                        select(BusinessConnection.business_connection_id).where(
                            BusinessConnection.user_telegram_id == payload.partner_id,
                            BusinessConnection.is_enabled.is_(True),
                        ).limit(1)
                    )).scalar_one_or_none()
                except Exception:
                    pass

                # ── Primary: send via PARTNER's business connection ────────
                # The message lands in the partner's conversation with the
                # owner with the Accept/Decline keyboard visible right there.
                _cat = payload.category if payload.category in ("friendship", "romantic") else "romantic"
                _req_text = (
                    f"💌 <b>{_name}</b> хочет с тобой подружиться! 👫\n\nПрими или отклони запрос:"
                    if _cat == "friendship" else
                    f"💕 <b>{_name}</b> хочет начать с тобой отношения!\n\nПрими или отклони запрос:"
                )
                _delivered = False
                if _partner_bc_id:
                    try:
                        await _bot.send_message(
                            owner_id,        # chat_id = owner, from partner's bc
                            _req_text,
                            parse_mode="HTML",
                            reply_markup=_kb,
                            business_connection_id=_partner_bc_id,
                        )
                        _delivered = True
                    except Exception as _pbc_exc:
                        logger.warning(
                            "rel_request: partner-bc delivery to %s failed: %s",
                            payload.partner_id, _pbc_exc,
                        )

                # ── Secondary: plain bot DM (requires partner to have /start) ─
                if not _delivered:
                    try:
                        await _bot.send_message(
                            payload.partner_id,
                            _req_text,
                            parse_mode="HTML",
                            reply_markup=_kb,
                        )
                        _delivered = True
                    except Exception as _dm_exc:
                        logger.warning(
                            "rel_request: DM to partner %s failed: %s",
                            payload.partner_id, _dm_exc,
                        )

                # ── Nudge in owner's chat regardless (plain text, no buttons) ─
                # Lets the owner know the request was sent and the partner
                # will see it on their end.
                if _owner_bc_id:
                    try:
                        await _bot.send_message(
                            payload.partner_id,
                            f"💌 <b>{_name}</b> отправил запрос дружбы!\n"
                            f"Партнёр получил уведомление с кнопками.",
                            parse_mode="HTML",
                            business_connection_id=_owner_bc_id,
                        )
                    except Exception:
                        pass

                # ── Fallback: alert owner when partner was not reachable ───
                if not _delivered:
                    _partner_not_reachable = True
                    _alert = (
                        "❌ Запрос отправлен в базе, но партнёру не удалось "
                        "доставить уведомление — он ещё не подключил бота."
                    )
                    if _owner_bc_id:
                        try:
                            await _bot.send_message(
                                payload.partner_id, _alert,
                                parse_mode="HTML",
                                business_connection_id=_owner_bc_id,
                            )
                        except Exception:
                            pass
                    try:
                        await _bot.send_message(owner_id, _alert, parse_mode="HTML")
                    except Exception:
                        pass
            except Exception as _notify_exc:
                logger.warning("rel_request: notification setup failed: %s", _notify_exc)

        result = repo.to_dict(rel, owner_id)
        if _partner_not_reachable:
            result["partner_not_reachable"] = True
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/app/api/relationships/respond")
async def rel_respond(
    payload: RelRespondRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    await _assert_subscribed(owner_id, session)
    repo     = RelationshipRepository(session)
    try:
        rel = await repo.respond(owner_id, payload.partner_id, payload.accept)
        await session.commit()
        if payload.accept:
            try:
                _bot = get_bot(settings)
                if _bot:
                    from app.models.user import TelegramUser as _TU
                    _me = (await session.execute(
                        select(_TU).where(_TU.telegram_user_id == owner_id)
                    )).scalar_one_or_none()
                    _parts = [p for p in [
                        _me.first_name if _me else None,
                        _me.last_name  if _me else None,
                    ] if p]
                    _name = " ".join(_parts) or f"#{owner_id}"
                    await _bot.send_message(
                        payload.partner_id,
                        f"💛 <b>{_name}</b> принял(а) твой запрос дружбы!\n"
                        f"Открой мини-приложение, чтобы отправить подарок.",
                        parse_mode="HTML",
                    )
            except Exception:
                pass
        return repo.to_dict(rel, owner_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/app/api/relationships/cancel")
async def rel_cancel(
    payload: RelPartnerRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    repo     = RelationshipRepository(session)
    try:
        await repo.cancel_request(owner_id, payload.partner_id)
        await session.commit()
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/app/api/relationships/gift")
async def rel_gift(
    payload: RelPartnerRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    await _assert_subscribed(owner_id, session)
    repo     = RelationshipRepository(session)
    try:
        result = await repo.gift(
            owner_id, payload.partner_id, payload.gift_id or "rose"
        )
        await session.commit()
        return result
    except ValueError as e:
        detail = str(e).split(":")[0]
        raise HTTPException(status_code=400, detail=detail) from e

@router.post("/app/api/relationships/postcard")
async def rel_postcard(
    payload: RelPostcardRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Generate and send a postcard image to the partner via direct bot message.
    Blocked for 24 h after each send (flag stored in relationship meta).

    Concurrency-safe pattern:
    1. Lock the relationship row FOR UPDATE.
    2. Check the 24-hour cooldown under the lock.
    3. Write the cooldown timestamp and COMMIT before sending (reservation).
    4. Attempt the Telegram send outside the holding lock.
    5. On send failure, reopen a new transaction to clear the reservation so
       the user can retry immediately.
    """
    import json as _json
    import datetime as _dt

    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    repo     = RelationshipRepository(session)
    partner_id = payload.partner_id

    # ── Step 1: lock the row and check cooldown atomically ────────────────────
    rel = await repo.get_between(owner_id, partner_id, lock=True)
    if rel is None or rel.status != "active":
        raise HTTPException(status_code=404, detail="relationship_not_found")

    _postcard_key = "last_postcard_a" if owner_id == rel.user_a_id else "last_postcard_b"
    try:
        _meta = _json.loads(rel.meta) if rel.meta else {}
    except Exception:
        _meta = {}

    _raw = _meta.get(_postcard_key)
    if _raw:
        try:
            _lp = _dt.datetime.fromisoformat(_raw)
            if _lp.tzinfo is None:
                _lp = _lp.replace(tzinfo=_dt.timezone.utc)
            if (_dt.datetime.now(_dt.timezone.utc) - _lp).total_seconds() < 86400:
                raise HTTPException(status_code=429, detail="postcard_cooldown")
        except HTTPException:
            raise
        except Exception:
            pass

    # ── Step 2: collect data we need while holding the lock ───────────────────
    days_together: int | None = None
    if rel.accepted_at:
        _acc = rel.accepted_at
        if _acc.tzinfo is None:
            _acc = _acc.replace(tzinfo=_dt.timezone.utc)
        days_together = max(0, (_dt.datetime.now(_dt.timezone.utc) - _acc).days)

    streak_days = _meta.get("streak", {}).get("days", 0)
    rel_type    = rel.rel_type

    # ── Step 3: reserve the cooldown BEFORE sending (commit releases the lock) ─
    _now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    _meta[_postcard_key] = _now_iso
    # Track total postcards sent in lifetime totals
    _totals = _meta.get("totals", {})
    _totals["postcards"] = _totals.get("postcards", 0) + 1
    _meta["totals"] = _totals
    rel.meta = _json.dumps(_meta, ensure_ascii=False)
    await session.commit()  # lock released here; concurrent requests now see the reservation

    # ── Step 4: fetch user names (no lock needed anymore) ─────────────────────
    from app.models.user import TelegramUser as _TU
    _users = (await session.execute(
        select(_TU).where(_TU.telegram_user_id.in_([owner_id, partner_id]))
    )).scalars().all()
    _name_map = {}
    for _u in _users:
        _parts = [p for p in [_u.first_name, _u.last_name] if p]
        _name_map[_u.telegram_user_id] = " ".join(_parts) or f"#{_u.telegram_user_id}"

    sender_name  = _name_map.get(owner_id, f"#{owner_id}")
    partner_name = _name_map.get(partner_id, f"#{partner_id}")

    # ── Step 5: generate postcard image ───────────────────────────────────────
    from app.services.postcard_service import render_postcard as _render
    from app.models.relationship import TIER_LABELS as _TL

    async def _clear_reservation() -> None:
        """Undo the cooldown reservation so the user can retry."""
        try:
            from sqlalchemy import text as _text
            async with session.begin():
                _rel2 = await repo.get_between(owner_id, partner_id, lock=True)
                if _rel2:
                    try:
                        _m2 = _json.loads(_rel2.meta) if _rel2.meta else {}
                    except Exception:
                        _m2 = {}
                    _m2.pop(_postcard_key, None)
                    _rel2.meta = _json.dumps(_m2, ensure_ascii=False)
        except Exception:
            logger.warning("could not clear postcard reservation owner=%s", owner_id)

    try:
        _img_bytes = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: _render(
                rel_type=rel_type,
                sender_name=sender_name,
                partner_name=partner_name,
                message="Отправлено из мини-приложения 💌",
                days_together=days_together,
                streak_days=streak_days,
            ),
        )
    except Exception as exc:
        logger.exception("postcard render failed for owner=%s partner=%s", owner_id, partner_id)
        await _clear_reservation()
        raise HTTPException(status_code=500, detail="render_failed") from exc

    # ── Step 6: send photo to partner ──────────────────────────────────────────
    _bot = get_bot(settings)
    if _bot is None:
        await _clear_reservation()
        raise HTTPException(status_code=503, detail="bot_unavailable")
    try:
        from aiogram.types import BufferedInputFile
        _caption = (
            f"💌 <b>{sender_name}</b> прислал(а) тебе открытку!\n"
            f"Тип отношений: <b>{_TL.get(rel_type, rel_type)}</b>"
        )
        await _bot.send_photo(
            partner_id,
            BufferedInputFile(_img_bytes, filename="postcard.png"),
            caption=_caption,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.exception("postcard send failed for owner=%s partner=%s", owner_id, partner_id)
        await _clear_reservation()
        raise HTTPException(status_code=500, detail="send_failed") from exc

    return {"ok": True}
@router.post("/app/api/relationships/quest-claim")
async def rel_quest_claim(
    payload: RelPartnerRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    repo     = RelationshipRepository(session)
    if not payload.quest_id:
        raise HTTPException(status_code=400, detail="quest_id_required")
    try:
        result = await repo.claim_quest(owner_id, payload.partner_id, payload.quest_id)
        await session.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/app/api/relationships/pair-stats")
async def rel_pair_stats(
    payload: RelPartnerRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Fun couple stats: message counts between owner and partner + a
    deterministic 'compatibility' score seeded by the pair ids."""
    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    repo     = RelationshipRepository(session)
    rel = await repo.get_between(owner_id, payload.partner_id)
    if not rel or rel.status != "active":
        raise HTTPException(status_code=400, detail="not_related")

    from app.models.business_connection import BusinessConnection as _BC
    from app.models.message import Message as _Msg
    conn_ids = [
        row[0] for row in (await session.execute(
            select(_BC.business_connection_id).where(
                _BC.user_telegram_id == owner_id
            )
        )).all()
    ]
    sent = received = 0
    if conn_ids:
        sent = (await session.execute(
            select(func.count()).select_from(_Msg).where(
                _Msg.business_connection_id.in_(conn_ids),
                _Msg.chat_id == payload.partner_id,
                _Msg.is_outgoing.is_(True),
            )
        )).scalar_one()
        received = (await session.execute(
            select(func.count()).select_from(_Msg).where(
                _Msg.business_connection_id.in_(conn_ids),
                _Msg.chat_id == payload.partner_id,
                _Msg.is_outgoing.is_(False),
            )
        )).scalar_one()

    total = sent + received
    balance = round(min(sent, received) / max(sent, received) * 100) if sent and received else 0
    # Deterministic playful score: stable per pair, nudged by real activity
    a, b = min(owner_id, payload.partner_id), max(owner_id, payload.partner_id)
    seed = (a * 31 + b * 17) % 41  # 0..40
    activity = min(30, total // 50)             # up to +30 for chatting
    balance_pts = round(balance * 0.29)          # up to +29 for symmetry
    compat = min(100, 40 + seed % 21 + activity + balance_pts)  # 40..100

    meta_totals = {}
    try:
        import json as _json
        meta_totals = (_json.loads(rel.meta) if rel.meta else {}).get("totals", {})
    except Exception:
        pass

    return {
        "messages_sent":     sent,
        "messages_received": received,
        "messages_total":    total,
        "balance_pct":       balance,
        "compatibility":     compat,
        "total_gifts":       meta_totals.get("gifts", 0),
        "total_spent":       meta_totals.get("spent", 0),
    }


@router.post("/app/api/relationships/upgrade")
async def rel_upgrade(
    payload: RelPartnerRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    repo     = RelationshipRepository(session)
    try:
        rel = await repo.upgrade_tier(owner_id, payload.partner_id)
        await session.commit()
        try:
            _bot = get_bot(settings)
            if _bot:
                from app.models.relationship import TIER_LABELS as _TL
                from app.models.user import TelegramUser as _TU
                _me = (await session.execute(
                    select(_TU).where(_TU.telegram_user_id == owner_id)
                )).scalar_one_or_none()
                _parts = [p for p in [
                    _me.first_name if _me else None,
                    _me.last_name  if _me else None,
                ] if p]
                _name = " ".join(_parts) or f"#{owner_id}"
                await _bot.send_message(
                    payload.partner_id,
                    f"🎉 <b>{_name}</b> развил(а) ваши отношения "
                    f"до <b>{_TL.get(rel.rel_type, rel.rel_type)}</b>!\n"
                    f"Открой мини-приложение, чтобы посмотреть детали.",
                    parse_mode="HTML",
                )
        except Exception:
            pass
        return repo.to_dict(rel, owner_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/app/api/relationships/change-category")
async def rel_change_category(
    payload: RelPartnerRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    repo     = RelationshipRepository(session)
    new_cat  = payload.new_category or ""
    if new_cat not in ("friendship", "romantic"):
        raise HTTPException(status_code=400, detail="invalid_category")
    try:
        rel = await repo.change_category(owner_id, payload.partner_id, new_cat)
        await session.commit()
        return repo.to_dict(rel, owner_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/app/api/relationships/break")
async def rel_break(
    payload: RelPartnerRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    settings = get_settings()
    owner_id = _verify_rel_init(payload.init_data, settings)
    repo     = RelationshipRepository(session)
    try:
        # Remember tier before breaking so we can notify if it was a marriage
        _rel_before = await repo.get_between(owner_id, payload.partner_id)
        _was_married = _rel_before and _rel_before.rel_type == "married" and _rel_before.status == "active"

        await repo.break_rel(owner_id, payload.partner_id)
        await session.commit()

        # Notify the partner when a marriage ends so they're not surprised
        if _was_married:
            try:
                _bot = get_bot(settings)
                if _bot:
                    from app.models.user import TelegramUser as _TU
                    _me = (await session.execute(
                        select(_TU).where(_TU.telegram_user_id == owner_id)
                    )).scalar_one_or_none()
                    _parts = [p for p in [
                        _me.first_name if _me else None,
                        _me.last_name  if _me else None,
                    ] if p]
                    _name = " ".join(_parts) or f"#{owner_id}"
                    await _bot.send_message(
                        payload.partner_id,
                        f"💔 <b>{_name}</b> расторг(ла) ваш брак.\n\n"
                        f"Ежедневный бонус 💍 +{MARRIAGE_DAILY_BONUS}🪙 больше не начисляется.",
                        parse_mode="HTML",
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to send marriage break-up notification to partner %s: %s",
                    payload.partner_id,
                    exc,
                )

        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


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
    sub_repo   = SubscriptionRepository(session)
    config     = await sub_repo.get_config()
    vip_config = await sub_repo.get_vip_config()
    sub        = await sub_repo.get_active_subscription(owner_id)
    return _sub_status_dict(sub, config, vip_config)


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

    # Use client-chosen plan if valid, else fall back to config defaults
    effective_stars = (
        payload.plan_stars
        if payload.plan_stars and payload.plan_stars >= 1
        else config.price_stars
    )
    effective_days = (
        payload.plan_days
        if payload.plan_days and payload.plan_days >= 1
        else config.duration_days
    )

    bot = get_bot(settings)
    try:
        invoice_link = await bot.create_invoice_link(
            title=config.title,
            description=config.description,
            # Encode duration so the payment handler activates the right plan
            payload=f"subscription_{owner_id}_{effective_days}",
            provider_token="",          # empty string = Telegram Stars (XTR)
            currency="XTR",
            prices=[LabeledPrice(label=config.title, amount=effective_stars)],
        )
    except Exception as exc:
        logger.exception("Failed to create invoice link for user %s", owner_id)
        raise HTTPException(status_code=502, detail="invoice_send_failed") from exc

    return {"ok": True, "price_stars": effective_stars, "invoice_link": invoice_link}


class CoinPackageInvoiceRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    package_id: str = Field(alias="packageId")


@router.post("/app/api/shop/coins/invoice")
async def shop_coins_invoice(payload: CoinPackageInvoiceRequest) -> dict:
    """Create a Telegram Stars invoice link for buying a coin package."""
    from aiogram.types import LabeledPrice

    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    owner_id = int(user["id"])

    pkg = COIN_PACKAGES.get(payload.package_id)
    if pkg is None:
        raise HTTPException(status_code=422, detail="unknown_package")

    bonus_text = f" (бонус {pkg['bonus']})" if pkg.get("bonus") else ""
    bot = get_bot(settings)
    try:
        invoice_link = await bot.create_invoice_link(
            title=f"{pkg['coins']:,} монет".replace(",", " "),
            description=f"Пополнение кошелька на {pkg['coins']} 🪙{bonus_text}",
            payload=f"coins_{owner_id}_{payload.package_id}",
            provider_token="",   # Telegram Stars
            currency="XTR",
            prices=[LabeledPrice(label=f"{pkg['coins']} монет", amount=pkg["stars"])],
        )
    except Exception as exc:
        logger.exception("Failed to create coins invoice for user %s", owner_id)
        raise HTTPException(status_code=502, detail="invoice_send_failed") from exc

    return {"ok": True, "invoice_link": invoice_link, "stars": pkg["stars"], "coins": pkg["coins"]}


@router.post("/app/api/subscription/vip/invoice")
async def vip_subscription_invoice(
    payload: SubscriptionInvoiceRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Create a Telegram Stars invoice for VIP subscription."""
    from aiogram.types import LabeledPrice

    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    owner_id = int(user["id"])

    sub_repo   = SubscriptionRepository(session)
    vip_config = await sub_repo.get_vip_config()
    if not vip_config.is_enabled:
        raise HTTPException(status_code=403, detail="subscription_disabled")

    # Check for existing active VIP sub
    existing = await sub_repo.get_active_vip_subscription(owner_id)
    if existing:
        raise HTTPException(status_code=409, detail="already_subscribed")

    effective_stars = (
        payload.plan_stars if payload.plan_stars and payload.plan_stars >= 1
        else vip_config.price_stars
    )
    effective_days = (
        payload.plan_days if payload.plan_days and payload.plan_days >= 1
        else vip_config.duration_days
    )

    bot = get_bot(settings)
    try:
        invoice_link = await bot.create_invoice_link(
            title=vip_config.title,
            description=vip_config.description,
            payload=f"vip_subscription_{owner_id}_{effective_days}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=vip_config.title, amount=effective_stars)],
        )
    except Exception as exc:
        logger.exception("Failed to create VIP invoice link for user %s", owner_id)
        raise HTTPException(status_code=502, detail="invoice_send_failed") from exc

    return {"ok": True, "price_stars": effective_stars, "invoice_link": invoice_link}


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
    """List subscription rows (paginated).

    When statusFilter is provided only that lifecycle status is returned;
    otherwise all rows (active + cancelled + refunded) are returned so the
    admin can see the full history.
    """
    _require_admin(payload.init_data)
    # Validate the status filter to prevent arbitrary column values.
    valid_statuses = {"active", "paused", "cancelled", "refunded"}
    status_filter = payload.status_filter if payload.status_filter in valid_statuses else None
    sub_repo = SubscriptionRepository(session)
    return await sub_repo.list_subscribers(page=payload.page, status_filter=status_filter)


@router.post("/app/api/admin/subscription/grant")
async def admin_subscription_grant(
    payload: AdminSubGrantRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Manually grant a subscription to a user."""
    _require_admin(payload.init_data)
    sub_type = payload.sub_type if payload.sub_type in ("premium", "vip") else "premium"
    sub_repo = SubscriptionRepository(session)
    sub = await sub_repo.grant(payload.owner_telegram_id, payload.duration_days, sub_type=sub_type)
    await session.commit()

    # Notify the user in their DM
    try:
        from app.bot import emoji as E
        _bot = get_bot(get_settings())
        if _bot:
            _expires = sub.expires_at.strftime("%d.%m.%Y")
            if sub_type == "vip":
                await _bot.send_message(
                    payload.owner_telegram_id,
                    f"💎 <b>VIP активирован!</b>\n\n"
                    f"Администратор выдал вам VIP на <b>{payload.duration_days} дн.</b>\n"
                    f"Действует до: <b>{_expires}</b>\n\n"
                    f"Добро пожаловать в VIP-клуб! 👑",
                    parse_mode="HTML",
                )
            else:
                await _bot.send_message(
                    payload.owner_telegram_id,
                    f"{E.STAR} <b>Premium активирован!</b>\n\n"
                    f"Администратор выдал вам Premium на <b>{payload.duration_days} дн.</b>\n"
                    f"Действует до: <b>{_expires}</b>\n\n"
                    f"Наслаждайтесь привилегиями! 🎉",
                    parse_mode="HTML",
                )
    except Exception as _exc:
        logger.warning("admin_grant: failed to notify user %s: %s",
                       payload.owner_telegram_id, _exc)

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

    # Notify the user in their DM
    try:
        from app.bot import emoji as E
        _bot = get_bot(get_settings())
        if _bot:
            await _bot.send_message(
                payload.owner_telegram_id,
                f"{E.WARNING} <b>Premium деактивирован</b>\n\n"
                f"Ваша Premium-подписка была отозвана администратором.",
                parse_mode="HTML",
            )
    except Exception as _exc:
        logger.warning("admin_revoke: failed to notify user %s: %s",
                       payload.owner_telegram_id, _exc)

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

    # Derive the current bot's Telegram ID from the token so the frontend can
    # label connections as "new bot" vs "old bot".
    try:
        _settings = get_settings()
        current_bot_id = int(_settings.telegram_bot_token.split(":")[0])
    except Exception:
        current_bot_id = None

    return {
        "total_users": overview.total_users,
        "active_users": overview.active_users,
        "current_bot_id": current_bot_id,
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
                "bot_id": u.bot_id,
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
        filters = MessageFilters(
            chat_id=payload.chat_id,
            connection_ids=connection_ids,
            text_query=payload.text_query or None,
        )
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


@router.post("/app/api/admin/search_chats")
async def admin_search_chats(
    payload: AdminSearchChatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    _require_admin(payload.init_data)

    query = payload.query.strip()
    if len(query) < 2:
        raise HTTPException(status_code=400, detail="Query too short")

    from sqlalchemy import text as _text  # noqa: PLC0415

    sql = _text("""
        WITH matches AS (
            SELECT m.id, m.chat_id, m.business_connection_id,
                   m.text, m.caption, m.media_type,
                   m.sender_first_name, m.sender_last_name, m.sender_username,
                   m.sent_at
            FROM   messages m
            WHERE  m.text ILIKE :like OR m.caption ILIKE :like
        ),
        chat_stats AS (
            SELECT chat_id, business_connection_id,
                   COUNT(*)     AS match_count,
                   MAX(sent_at) AS last_match_at
            FROM   matches
            GROUP  BY chat_id, business_connection_id
        ),
        latest_msg AS (
            SELECT DISTINCT ON (chat_id, business_connection_id)
                   id, chat_id, business_connection_id,
                   text, caption, media_type,
                   sender_first_name, sender_last_name, sender_username
            FROM   matches
            ORDER  BY chat_id, business_connection_id, sent_at DESC
        )
        SELECT cs.chat_id,
               cs.match_count,
               cs.last_match_at,
               lm.text              AS latest_text,
               lm.caption           AS latest_caption,
               lm.sender_first_name,
               lm.sender_last_name,
               lm.sender_username,
               bc.user_telegram_id  AS owner_telegram_id,
               bc.user_username     AS owner_username
        FROM   chat_stats  cs
        JOIN   latest_msg  lm ON cs.chat_id = lm.chat_id
                               AND cs.business_connection_id = lm.business_connection_id
        JOIN   business_connections bc ON bc.business_connection_id = cs.business_connection_id
        ORDER  BY cs.last_match_at DESC
        LIMIT  100
    """)
    try:
        result = await session.execute(sql, {"like": f"%{query}%"})
        rows = [dict(r._mapping) for r in result]
        return {
            "results": [
                {
                    "chat_id":           r["chat_id"],
                    "match_count":       r["match_count"],
                    "last_match_at":     r["last_match_at"].isoformat() if r["last_match_at"] else None,
                    "latest_text":       r["latest_text"],
                    "latest_caption":    r["latest_caption"],
                    "sender_first_name": r["sender_first_name"],
                    "sender_last_name":  r["sender_last_name"],
                    "sender_username":   r["sender_username"],
                    "owner_telegram_id": r["owner_telegram_id"],
                    "owner_username":    r["owner_username"],
                }
                for r in rows
            ]
        }
    except Exception:
        logger.exception("admin_search_chats failed for query=%r", query)
        raise HTTPException(status_code=500, detail="Search failed") from None


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

    # Build optional inline keyboard
    reply_markup = None
    if payload.buttons:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup  # noqa: PLC0415
        kb_rows = []
        for row in payload.buttons:
            kb_row = [
                InlineKeyboardButton(text=btn["text"], url=btn["url"])
                for btn in row
                if btn.get("text") and btn.get("url")
            ]
            if kb_row:
                kb_rows.append(kb_row)
        if kb_rows:
            reply_markup = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    sent = 0
    failed = 0
    for owner_id in owner_ids:
        try:
            await bot.send_message(
                chat_id=owner_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
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
    """Return active boosts + settings + price list for the shop tab.
    Includes `prices_version` — a short hash of the price config — so clients
    can detect admin-side price changes without a full re-fetch."""
    settings = get_settings()
    owner_id = _shop_auth(payload.resolved_init, settings)
    repo = ShopRepository(session)
    return await repo.get_shop_status(owner_id)


@router.post("/app/api/shop/prices-version")
async def shop_prices_version(
    payload: ShopStatusRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Lightweight endpoint: returns only the current prices_version hash.
    Clients use this to validate their cached shop data without fetching the
    full payload — O(1) DB read, no per-user joins."""
    settings = get_settings()
    _shop_auth(payload.resolved_init, settings)
    repo = ShopRepository(session)
    return {"prices_version": await repo.get_prices_version()}


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
    ok, ref = await repo.admin_set_status(payload.referral_id, payload.status, payload.reason)
    if not ok:
        raise HTTPException(status_code=404, detail="Referral not found")
    # Phase 1: commit the status change.
    await session.commit()

    if payload.status == "active" and ref is not None:
        # Phase 1b: grant per-activation reward for the referrer and welcome
        # reward for the referee — the same rewards that try_activate grants on
        # the normal mini-app path.  Idempotent: skips any reward type already
        # logged for this referral_id, so re-activating an already-active row
        # never double-grants.
        activation_rewards = await repo.admin_grant_per_activation_rewards(ref)
        if activation_rewards:
            await session.commit()
            logger.info(
                "Admin import: granted %d activation reward(s) for "
                "referral_id=%s (referrer=%s, referred=%s)",
                len(activation_rewards),
                ref.id,
                ref.referrer_telegram_id,
                ref.referred_telegram_id,
            )

        # Phase 2: evaluate milestones for the referrer NOW (after commit) so
        # that _count_active reads fully committed state — preventing the TOCTOU
        # milestone-skip race.
        ms_rewards = await repo.evaluate_and_grant_milestones(
            ref.referrer_telegram_id, ref.id
        )
        if ms_rewards:
            await session.commit()
            logger.info(
                "Admin import: granted %d milestone reward(s) for referrer=%s "
                "after activating referral_id=%s",
                len(ms_rewards),
                ref.referrer_telegram_id,
                ref.id,
            )

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


@router.post("/app/api/admin/relationships/cancel-pending")
async def admin_cancel_pending_relationships(
    payload: AdminInitDataOnlyRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Admin one-shot: mark all stuck 'pending' relationship rows as 'broken'.

    Use this to clean up requests that were saved to DB but whose notification
    never reached the partner (e.g. due to the reply_markup/business-chat bug).
    After this call users can send fresh friend requests to the same people.
    """
    admin_user = _require_admin(payload.init_data)

    result = await session.execute(
        text(
            "UPDATE relationships SET status = 'broken' "
            "WHERE status = 'pending' "
            "RETURNING id, user_a_id, user_b_id, initiator_id"
        )
    )
    rows = result.fetchall()
    cancelled = len(rows)

    session.add(AdminActionLog(
        admin_username=admin_user.get("username", "?"),
        action="cancel_pending_relationships",
        target_owner_telegram_id=None,
        details=f"cancelled {cancelled} pending relationship requests",
    ))
    await session.commit()

    logger.info(
        "Admin %s cancelled %d pending relationship requests",
        admin_user.get("username", "?"),
        cancelled,
    )
    return {
        "ok": True,
        "cancelled": cancelled,
        "rows": [
            {"id": r.id, "user_a_id": r.user_a_id, "user_b_id": r.user_b_id,
             "initiator_id": r.initiator_id}
            for r in rows
        ],
    }


# ── AI Relationship Analysis ──────────────────────────────────────────────────

class AiAnalysisRequest(BaseModel):
    init_data:    str = Field(...,              alias="initData")
    chat_id:      int = Field(...,              alias="chatId")
    contact_name: str = Field("Собеседник",    alias="contactName")
    model_config = {"populate_by_name": True}


@router.post("/app/api/ai/relationship_analysis")
async def ai_relationship_analysis(
    payload: AiAnalysisRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Run Gemini AI analysis for a specific chat. VIP-only."""
    from app.models.business_connection import BusinessConnection   # noqa: PLC0415
    from app.services.ai_analysis_service import (  # noqa: PLC0415
        analyze, get_remaining, DAILY_ANALYSIS_LIMIT,
    )

    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")
    owner_id = int(user["id"])

    sub_repo = SubscriptionRepository(session)
    vip_sub  = await sub_repo.get_active_vip_subscription(owner_id)
    if vip_sub is None:
        raise HTTPException(status_code=403, detail="vip_required")

    conn_result = await session.execute(
        select(BusinessConnection.business_connection_id).where(
            BusinessConnection.user_telegram_id == owner_id
        )
    )
    connection_ids = [row[0] for row in conn_result.all()]
    if not connection_ids:
        raise HTTPException(status_code=404, detail="no_connection")

    try:
        result = await analyze(
            session=session,
            owner_id=owner_id,
            chat_id=payload.chat_id,
            connection_ids=connection_ids,
            contact_name=payload.contact_name,
        )
    except ValueError as exc:
        detail = str(exc)
        if detail == "no_messages":
            raise HTTPException(status_code=404, detail="no_messages") from exc
        if "GEMINI_API_KEY" in detail:
            raise HTTPException(status_code=503, detail="ai_not_configured") from exc
        if detail == "gemini_quota":
            raise HTTPException(status_code=503, detail="gemini_quota") from exc
        if detail == "gemini_rate_limit":
            raise HTTPException(status_code=429, detail="gemini_rate_limit") from exc
        if detail == "gemini_timeout":
            raise HTTPException(status_code=504, detail="gemini_timeout") from exc
        # Pass through real error message for debugging
        logger.exception("AI analysis ValueError for user=%s chat=%s: %s", owner_id, payload.chat_id, detail)
        raise HTTPException(status_code=500, detail=f"analysis_failed: {detail}") from exc
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("AI analysis failed for user=%s chat=%s: %s", owner_id, payload.chat_id, msg)
        raise HTTPException(status_code=500, detail=f"analysis_failed: {msg}") from exc

    result["analyses_remaining"] = await get_remaining(session, owner_id)
    result["analyses_limit"] = DAILY_ANALYSIS_LIMIT
    return result


class ContactProfileRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    chat_id:   int = Field(..., alias="chatId")
    model_config = {"populate_by_name": True}


@router.post("/app/api/contact/profile")
async def contact_profile_info(
    payload: ContactProfileRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return subscription + frame + streak-mute info for a contact."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")

    owner_id = user["id"]

    sub_repo = SubscriptionRepository(session)
    sub      = await sub_repo.get_active_subscription(payload.chat_id)
    vip_sub  = await sub_repo.get_active_vip_subscription(payload.chat_id)

    from app.models.user_settings import UserSettings as _US  # noqa: PLC0415
    # Contact's settings (for frame/subscription badge)
    contact_us = (await session.execute(
        select(_US).where(_US.owner_telegram_id == payload.chat_id)
    )).scalar_one_or_none()

    # Owner's settings (for streak mute list)
    owner_us = (await session.execute(
        select(_US).where(_US.owner_telegram_id == owner_id)
    )).scalar_one_or_none()

    muted = list(owner_us.muted_streaks or []) if owner_us else []
    streak_muted = payload.chat_id in muted

    return {
        "is_vip":        vip_sub is not None,
        "is_premium":    sub is not None,
        "frame":         (contact_us.frame if contact_us else "none") or "none",
        "theme":         (contact_us.theme if contact_us else "default") or "default",
        "streak_muted":  streak_muted,
    }


class StreakMuteRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    chat_id:   int = Field(..., alias="chatId")
    muted:     bool
    model_config = {"populate_by_name": True}


@router.post("/app/api/contact/streak-mute")
async def contact_streak_mute(
    payload: StreakMuteRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Toggle streak notifications for a specific contact on/off."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")

    owner_id = user["id"]

    from app.models.user_settings import UserSettings as _US  # noqa: PLC0415
    us = (await session.execute(
        select(_US).where(_US.owner_telegram_id == owner_id)
    )).scalar_one_or_none()

    if us is None:
        us = _US(owner_telegram_id=owner_id)
        session.add(us)

    muted = list(us.muted_streaks or [])
    if payload.muted and payload.chat_id not in muted:
        muted.append(payload.chat_id)
    elif not payload.muted and payload.chat_id in muted:
        muted.remove(payload.chat_id)

    us.muted_streaks = muted
    await session.commit()

    return {"streak_muted": payload.chat_id in muted}


# ── User Settings GET / POST ───────────────────────────────────────────────────

class UpdateSettingRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    key:       str
    value:     object   # bool | str | list – validated per key in the handler
    model_config = {"populate_by_name": True}

_ALLOWED_SETTING_KEYS = {
    "streak_reminders_enabled",
    "dl_contact_videos",
    "dl_contact_videos_mutual",
    "chat_whitelist",   # repurposed as exclusion list (chats to skip)
}


@router.get("/app/api/settings")
async def get_user_settings(
    initData: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return all user-configurable settings."""
    settings = get_settings()
    user = verify_init_data(initData, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")

    owner_id = user["id"]
    from app.models.user_settings import UserSettings as _US  # noqa: PLC0415
    us = (await session.execute(
        select(_US).where(_US.owner_telegram_id == owner_id)
    )).scalar_one_or_none()

    if us is None:
        return {
            "streak_reminders_enabled":  True,
            "dl_contact_videos":         True,
            "dl_contact_videos_mutual":  False,
            "chat_exclusions":           [],
            "muted_streaks":             [],
        }

    return {
        "streak_reminders_enabled":  getattr(us, "streak_reminders_enabled", True),
        "dl_contact_videos":         getattr(us, "dl_contact_videos", True),
        "dl_contact_videos_mutual":  getattr(us, "dl_contact_videos_mutual", False),
        "chat_exclusions":           list(getattr(us, "chat_whitelist", None) or []),
        "muted_streaks":             list(us.muted_streaks or []),
    }


@router.post("/app/api/settings")
async def update_user_setting(
    payload: UpdateSettingRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Update a single user-configurable setting."""
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")

    if payload.key not in _ALLOWED_SETTING_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown setting: {payload.key}")

    owner_id = user["id"]
    from app.models.user_settings import UserSettings as _US  # noqa: PLC0415
    us = (await session.execute(
        select(_US).where(_US.owner_telegram_id == owner_id)
    )).scalar_one_or_none()
    if us is None:
        us = _US(owner_telegram_id=owner_id)
        session.add(us)

    # Validate and coerce value per key
    val = payload.value
    if payload.key in {"streak_reminders_enabled", "dl_contact_videos", "dl_contact_videos_mutual"}:
        val = bool(val)
    elif payload.key == "chat_whitelist":
        val = [int(x) for x in (val or [])]

    setattr(us, payload.key, val)
    await session.commit()
    return {"ok": True, payload.key: val}


class AiCacheInvalidateRequest(BaseModel):
    init_data:  str       = Field(...,        alias="initData")
    owner_id:   int       = Field(...,        alias="ownerId")
    chat_id:    int | None = Field(default=None, alias="chatId")
    model_config = {"populate_by_name": True}


@router.post("/app/api/admin/ai/invalidate_cache")
async def admin_ai_invalidate_cache(
    payload: AiCacheInvalidateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Manually invalidate a cached AI analysis (admin only).

    - If ``chatId`` is provided, only that (owner, chat) entry is removed.
    - If ``chatId`` is omitted, ALL cached analyses for the owner are removed.
    """
    from app.services.ai_analysis_service import (  # noqa: PLC0415
        invalidate_cache, invalidate_cache_for_owner,
    )

    admin_user = _require_admin(payload.init_data)

    if payload.chat_id is not None:
        await invalidate_cache(session, payload.owner_id, payload.chat_id)
        scope = f"chat={payload.chat_id}"
    else:
        await invalidate_cache_for_owner(session, payload.owner_id)
        scope = "all chats"

    logger.info(
        "Admin @%s invalidated AI analysis cache for owner=%s scope=%s",
        admin_user.get("username"),
        payload.owner_id,
        scope,
    )
    return {"ok": True, "owner_id": payload.owner_id, "scope": scope}


# ── DB stats / cleanup (admin) ────────────────────────────────────────────────

@router.post("/app/api/admin/db/stats")
async def admin_db_stats(
    payload: AdminInitDataOnlyRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Return per-table sizes + total DB size. PostgreSQL only."""
    _require_admin(payload.init_data)
    from app.services.media_cache_service import get_table_sizes  # noqa: PLC0415
    return await get_table_sizes(session)


@router.post("/app/api/admin/db/cleanup")
async def admin_db_cleanup(
    payload: AdminDbCleanupRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Purge media cache older than TTL and run VACUUM ANALYZE."""
    admin_user = _require_admin(payload.init_data)
    from app.services.media_cache_service import (  # noqa: PLC0415
        purge_old_media_cache, vacuum_tables,
    )
    deleted_cache = await purge_old_media_cache(session)
    await vacuum_tables(["media_cache"])
    logger.info("Admin @%s ran media_cache cleanup: deleted=%d", admin_user.get("username"), deleted_cache)
    return {"ok": True, "deleted_cache": deleted_cache}


@router.post("/app/api/admin/db/vacuum_full")
async def admin_vacuum_full(
    payload: AdminInitDataOnlyRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Run VACUUM FULL ANALYZE on all user tables + CHECKPOINT to reclaim OS disk space."""
    from sqlalchemy import text  # noqa: PLC0415
    from app.database.session import get_engine  # noqa: PLC0415

    admin_user = _require_admin(payload.init_data)
    engine = get_engine()
    async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        # CHECKPOINT flushes WAL and helps release WAL segment files
        await conn.execute(text("CHECKPOINT"))
        # VACUUM FULL on all user tables (including their TOAST tables)
        result = await conn.execute(text(
            "SELECT relname FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC"
        ))
        tables = [row[0] for row in result.fetchall()]
        for tbl in tables:
            await conn.execute(text(f"VACUUM FULL ANALYZE {tbl}"))
            logger.info("VACUUM FULL ANALYZE %s complete", tbl)
        await conn.execute(text("CHECKPOINT"))

    logger.info("Admin @%s ran full VACUUM on %d tables", admin_user.get("username"), len(tables))
    return {"ok": True, "tables_vacuumed": tables}


@router.post("/app/api/admin/db/wipe_media_cache")
async def admin_wipe_media_cache(
    payload: AdminInitDataOnlyRequest,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Delete ALL media_cache rows regardless of age, then VACUUM."""
    from sqlalchemy import delete as sa_delete, text  # noqa: PLC0415
    from app.models.media_cache import MediaCache  # noqa: PLC0415
    from app.services.media_cache_service import vacuum_tables  # noqa: PLC0415

    admin_user = _require_admin(payload.init_data)
    result = await session.execute(sa_delete(MediaCache))
    deleted = result.rowcount or 0
    await session.commit()
    await vacuum_tables(["media_cache"], full=True)
    logger.info("Admin @%s wiped ALL media_cache: %d rows deleted", admin_user.get("username"), deleted)
    return {"ok": True, "deleted_cache": deleted}


# ══════════════════════════════════════════════════════════════════════════════
# GIVEAWAY
# ══════════════════════════════════════════════════════════════════════════════

class GiveawayRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")


class GiveawayUpdateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    init_data: str = Field(alias="initData")
    deadline: str | None = Field(default=None)          # ISO-8601 or None to clear
    opens_at: str | None = Field(default=None, alias="opensAt")  # ISO-8601 or None to clear
    prize_1: str | None = Field(default=None, alias="prize1")
    prize_2: str | None = Field(default=None, alias="prize2")
    prize_3: str | None = Field(default=None, alias="prize3")
    prize_1_image: str | None = Field(default=None, alias="prize1Image")
    prize_2_image: str | None = Field(default=None, alias="prize2Image")
    prize_3_image: str | None = Field(default=None, alias="prize3Image")
    description: str | None = Field(default=None)
    is_visible_to_all: bool | None = Field(default=None, alias="isVisibleToAll")
    is_active: bool | None = Field(default=None, alias="isActive")


@router.post("/app/api/giveaway")
async def giveaway_info(
    payload: GiveawayRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Return giveaway config + top-3 referrers.

    Non-admins only see data when is_visible_to_all=True.
    Admin always sees data regardless of visibility flag.
    """
    settings = get_settings()
    user = verify_init_data(payload.init_data, settings.telegram_bot_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid init data")

    username = (user.get("username") or "").lower().lstrip("@")
    is_admin = username == settings.miniapp_admin_username.lower()

    repo = GiveawayRepository(session)
    cfg  = await repo.get_config()

    # Non-admins are locked out unless explicitly visible OR opens_at has passed
    now_utc = dt.datetime.now(dt.timezone.utc)
    opened_by_schedule = cfg.opens_at is not None and cfg.opens_at <= now_utc
    if not is_admin and not cfg.is_visible_to_all and not opened_by_schedule:
        return {
            "locked": True,
            "opens_at": cfg.opens_at.isoformat() if cfg.opens_at else None,
        }

    user_id: int | None = user.get("id")

    async def _no_rank() -> dict:
        return {"rank": None, "referral_count": 0, "total_participants": 0, "next_threshold": None}

    top, me = await asyncio.gather(
        repo.get_top_referrers(limit=3),
        repo.get_user_rank(user_id) if user_id else _no_rank(),
    )
    return {
        "locked": False,
        "is_admin": is_admin,
        **_giveaway_cfg_dict(cfg),
        "top": top,
        "me": me,
    }


@router.post("/app/api/admin/giveaway/config")
async def admin_giveaway_config(
    payload: GiveawayRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    _require_admin(payload.init_data)
    repo = GiveawayRepository(session)
    cfg  = await repo.get_config()
    top  = await repo.get_top_referrers(limit=3)
    return {**_giveaway_cfg_dict(cfg), "top": top}


@router.post("/app/api/admin/giveaway/update")
async def admin_giveaway_update(
    payload: GiveawayUpdateRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    _require_admin(payload.init_data)
    updates: dict = {}
    if payload.is_active is not None:
        updates["is_active"] = payload.is_active
    if payload.is_visible_to_all is not None:
        updates["is_visible_to_all"] = payload.is_visible_to_all
    if payload.description is not None:
        updates["description"] = payload.description.strip() or None
    if "prize1" in payload.model_fields_set or payload.prize_1 is not None:
        updates["prize_1"] = (payload.prize_1 or "").strip() or None
    if "prize2" in payload.model_fields_set or payload.prize_2 is not None:
        updates["prize_2"] = (payload.prize_2 or "").strip() or None
    if "prize3" in payload.model_fields_set or payload.prize_3 is not None:
        updates["prize_3"] = (payload.prize_3 or "").strip() or None
    if "prize1Image" in payload.model_fields_set or payload.prize_1_image is not None:
        updates["prize_1_image"] = (payload.prize_1_image or "").strip() or None
    if "prize2Image" in payload.model_fields_set or payload.prize_2_image is not None:
        updates["prize_2_image"] = (payload.prize_2_image or "").strip() or None
    if "prize3Image" in payload.model_fields_set or payload.prize_3_image is not None:
        updates["prize_3_image"] = (payload.prize_3_image or "").strip() or None
    if "deadline" in payload.model_fields_set:
        if payload.deadline:
            try:
                updates["deadline"] = dt.datetime.fromisoformat(payload.deadline).replace(
                    tzinfo=dt.timezone.utc
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid deadline format")
        else:
            updates["deadline"] = None
    if "opens_at" in payload.model_fields_set:
        if payload.opens_at:
            try:
                updates["opens_at"] = dt.datetime.fromisoformat(payload.opens_at).replace(
                    tzinfo=dt.timezone.utc
                )
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid opens_at format")
        else:
            updates["opens_at"] = None

    repo = GiveawayRepository(session)
    cfg  = await repo.update_config(**updates)
    await session.commit()
    top  = await repo.get_top_referrers(limit=3)
    return {"ok": True, **_giveaway_cfg_dict(cfg), "top": top}


def _giveaway_cfg_dict(cfg) -> dict:
    return {
        "is_active": cfg.is_active,
        "is_visible_to_all": cfg.is_visible_to_all,
        "deadline": cfg.deadline.isoformat() if cfg.deadline else None,
        "opens_at": cfg.opens_at.isoformat() if cfg.opens_at else None,
        "prize_1": cfg.prize_1,
        "prize_2": cfg.prize_2,
        "prize_3": cfg.prize_3,
        "prize_1_image": cfg.prize_1_image,
        "prize_2_image": cfg.prize_2_image,
        "prize_3_image": cfg.prize_3_image,
        "description": cfg.description,
        "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Admin — channel subscription gate management
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/app/api/admin/channels")
async def admin_channels_list(
    payload: StatsRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Return all required channels (active and inactive) with live bot-access status."""
    _require_admin(payload.init_data)
    from app.repositories.channel_repository import ChannelRepository  # noqa: PLC0415
    from app.services.channel_subscription_service import check_bot_access  # noqa: PLC0415
    repo = ChannelRepository(session)
    channels = await repo.get_all()

    # Probe bot access for all channels concurrently.
    # get_me() is called once and reused so we pay one round-trip, not N.
    _settings = get_settings()
    _bot = get_bot(_settings)
    try:
        _bot_me = await _bot.get_me()
        _bot_id: int | None = _bot_me.id
    except Exception:
        _bot_id = None

    access_results = await asyncio.gather(
        *(check_bot_access(_bot, ch, bot_id=_bot_id) for ch in channels),
        return_exceptions=True,
    )

    return {
        "channels": [
            {
                "id":             ch.id,
                "username":       ch.channel_username,
                "title":          ch.display_title,
                "join_url":       ch.join_url,
                "is_active":      ch.is_active,
                "created_at":     ch.created_at.isoformat(),
                "bot_has_access": bool(acc) if not isinstance(acc, Exception) else True,
            }
            for ch, acc in zip(channels, access_results)
        ]
    }


@router.post("/app/api/admin/channels/add")
async def admin_channels_add(
    payload: AdminChannelAddRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Add a new required channel."""
    _require_admin(payload.init_data)
    from app.repositories.channel_repository import ChannelRepository  # noqa: PLC0415
    username = payload.username.lstrip("@").strip()
    if not username:
        raise HTTPException(status_code=400, detail="username is required")
    repo = ChannelRepository(session)
    ch = await repo.add(username, payload.title or None)
    await session.commit()
    return {
        "ok": True,
        "channel": {
            "id":        ch.id,
            "username":  ch.channel_username,
            "title":     ch.display_title,
            "join_url":  ch.join_url,
            "is_active": ch.is_active,
        },
    }


@router.post("/app/api/admin/channels/toggle")
async def admin_channels_toggle(
    payload: AdminChannelActionRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Toggle a channel active / inactive."""
    _require_admin(payload.init_data)
    from app.repositories.channel_repository import ChannelRepository  # noqa: PLC0415
    repo = ChannelRepository(session)
    ch = await repo.toggle(payload.channel_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Channel not found")
    await session.commit()
    return {"ok": True, "is_active": ch.is_active}


@router.post("/app/api/admin/channels/delete")
async def admin_channels_delete(
    payload: AdminChannelActionRequest, session: AsyncSession = Depends(get_db_session)
) -> dict:
    """Delete a required channel."""
    _require_admin(payload.init_data)
    from app.repositories.channel_repository import ChannelRepository  # noqa: PLC0415
    repo = ChannelRepository(session)
    ok = await repo.delete(payload.channel_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Channel not found")
    await session.commit()
    return {"ok": True}


@router.post("/app/api/ai/ping")
async def ai_ping(payload: dict, session: AsyncSession = Depends(get_db_session)) -> dict:
    """Quick Gemini connectivity test (admin/debug only)."""
    import asyncio  # noqa: PLC0415
    import os       # noqa: PLC0415
    try:
        from google import genai  # noqa: PLC0415
    except ImportError as exc:
        return {"ok": False, "error": f"ImportError: {exc}"}

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return {"ok": False, "error": "GEMINI_API_KEY not set"}

    try:
        client = genai.Client(api_key=api_key)
        resp = await asyncio.to_thread(
            client.models.generate_content, model="gemini-flash-latest", contents="Say OK"
        )
        return {"ok": True, "response": resp.text[:100]}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
