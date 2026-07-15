"""Automatic streak notifications sent as bot DMs to the owner.

Two kinds of notification:
- **Success**: first incoming message of the day from a contact that continues
  a streak of ≥ 3 days → "🔥 Серия N дней продолжается!"
- **Reminder**: background loop finds streaks ≥ 3 days with no message today
  and sends a nudge during the evening reminder window (17–21 UTC).

Both use in-memory dicts to avoid duplicate DMs on the same calendar day.
"""
from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

MIN_STREAK = 3  # minimum streak length before we send any notification

# Dedup guards: key = (owner_id, chat_id) → date last notified
_success_sent: dict[tuple[int, int], dt.date] = {}
_remind_sent:  dict[tuple[int, int], dt.date] = {}

# Reminder window: only send between these UTC hours (inclusive start, exclusive end)
_REMIND_HOUR_START = 17
_REMIND_HOUR_END   = 21


# ── Helpers ───────────────────────────────────────────────────────────────────

def _calculate_streak(active_dates: set[dt.date]) -> int:
    """Consecutive-day streak ending today or yesterday (same logic as stats_repository)."""
    if not active_dates:
        return 0
    today = dt.date.today()
    most_recent = max(active_dates)
    if most_recent < today - dt.timedelta(days=1):
        return 0
    streak = 0
    day = today
    while day in active_dates:
        streak += 1
        day -= dt.timedelta(days=1)
    return streak


async def _get_active_dates(
    session: AsyncSession, connection_ids: list[str], chat_id: int
) -> set[dt.date]:
    from app.models.message import Message
    rows = await session.execute(
        select(func.date(Message.sent_at))
        .where(
            Message.business_connection_id.in_(connection_ids),
            Message.chat_id == chat_id,
        )
        .distinct()
    )
    return {r[0] for r in rows.fetchall()}


async def _count_today(
    session: AsyncSession, connection_ids: list[str], chat_id: int
) -> int:
    from app.models.message import Message
    today = dt.date.today()
    result = await session.execute(
        select(func.count(Message.id)).where(
            Message.business_connection_id.in_(connection_ids),
            Message.chat_id == chat_id,
            func.date(Message.sent_at) == today,
        )
    )
    return result.scalar_one()


# ── Success notification ───────────────────────────────────────────────────────

def _success_text(name: str, days: int) -> str:
    if days >= 100:
        return (
            f"🏆 <b>Серия 100+ дней с {name}!</b>\n"
            f"Сегодня {days}-й день подряд — это легенда 👑"
        )
    if days >= 30:
        return (
            f"🚀 <b>Серия {days} дней с {name}</b>\n"
            f"Месяц и больше — марафон продолжается 💪"
        )
    if days >= 14:
        return (
            f"🔥🔥 <b>Серия {days} дней с {name}</b>\n"
            f"Две недели подряд — так держать!"
        )
    if days >= 7:
        return (
            f"🔥 <b>Серия {days} дней с {name}</b>\n"
            f"Целая неделя без пропусков!"
        )
    return (
        f"🔥 <b>Серия {days} дней с {name} продолжается!</b>\n"
        f"Так держать 😊"
    )


async def maybe_notify_streak_continued(
    bot: Any,
    session: AsyncSession,
    owner_id: int,
    connection_ids: list[str],
    chat_id: int,
    contact_name: str,
) -> None:
    """Call after ingesting an incoming message. Sends DM if first msg today on a 3+ day streak."""
    today = dt.date.today()
    key = (owner_id, chat_id)
    if _success_sent.get(key) == today:
        return  # already notified today

    # Only on the very first message of today from this chat
    if await _count_today(session, connection_ids, chat_id) != 1:
        return

    dates = await _get_active_dates(session, connection_ids, chat_id)
    streak = _calculate_streak(dates)
    if streak < MIN_STREAK:
        return

    _success_sent[key] = today
    try:
        await bot.send_message(owner_id, _success_text(contact_name, streak), parse_mode="HTML")
        logger.info("Streak success notification sent owner=%s chat=%s streak=%s", owner_id, chat_id, streak)
    except Exception as exc:
        logger.warning("Streak success DM failed owner=%s chat=%s: %s", owner_id, chat_id, exc)


# ── Reminder loop ─────────────────────────────────────────────────────────────

def _remind_text(name: str, days: int) -> str:
    if days >= 30:
        return (
            f"⏰ <b>Серия {days} дней с {name} под угрозой!</b>\n"
            f"Напиши сегодня, чтобы не потерять марафон 🏃"
        )
    if days >= 7:
        return (
            f"⏰ <b>Не забудь написать {name}</b>\n"
            f"Серия {days} дней — напиши сегодня, чтобы не прервать 🔥"
        )
    return (
        f"⏰ <b>Напомни о себе {name}</b>\n"
        f"Серия {days} дней — напиши что-нибудь сегодня 😊"
    )


async def run_reminder_check(bot: Any) -> None:
    """Scan all active users and remind those with at-risk 3+ day streaks.

    Runs inside the background loop in main.py. Creates its own DB session.
    Only fires if current UTC hour is within the reminder window.
    """
    now_utc = dt.datetime.utcnow()
    if not (_REMIND_HOUR_START <= now_utc.hour < _REMIND_HOUR_END):
        return

    today = dt.date.today()

    from app.database.session import get_db_session
    from app.models.business_connection import BusinessConnection as BCModel
    from app.models.message import Message
    from app.models.user import TelegramUser

    async for session in get_db_session():
        try:
            # All active owner_ids
            owner_rows = await session.execute(
                select(BCModel.user_telegram_id)
                .where(BCModel.is_blocked.is_(False))
                .distinct()
            )
            owner_ids = [r[0] for r in owner_rows.fetchall()]

            for owner_id in owner_ids:
                conn_rows = await session.execute(
                    select(BCModel.business_connection_id).where(
                        BCModel.user_telegram_id == owner_id,
                        BCModel.is_blocked.is_(False),
                    )
                )
                connection_ids = [r[0] for r in conn_rows.fetchall()]
                if not connection_ids:
                    continue

                # Find distinct chat_ids with messages in the last 60 days
                cutoff = dt.datetime.utcnow() - dt.timedelta(days=60)
                chat_rows = await session.execute(
                    select(Message.chat_id)
                    .where(
                        Message.business_connection_id.in_(connection_ids),
                        Message.sent_at >= cutoff,
                    )
                    .distinct()
                )
                chat_ids = [r[0] for r in chat_rows.fetchall() if r[0] != owner_id]

                best_streak = 0
                best_chat_id = None

                for chat_id in chat_ids:
                    key = (owner_id, chat_id)
                    if _remind_sent.get(key) == today:
                        continue

                    # Check if already messaged today
                    if await _count_today(session, connection_ids, chat_id) > 0:
                        continue

                    dates = await _get_active_dates(session, connection_ids, chat_id)
                    streak = _calculate_streak(dates)
                    if streak >= MIN_STREAK and streak > best_streak:
                        best_streak = streak
                        best_chat_id = chat_id

                if best_chat_id is None:
                    continue

                # Resolve contact name
                user_row = await session.execute(
                    select(TelegramUser).where(TelegramUser.telegram_id == best_chat_id)
                )
                user_obj = user_row.scalar_one_or_none()
                if user_obj:
                    parts = [p for p in [user_obj.first_name, user_obj.last_name] if p]
                    name = " ".join(parts) or f"#{best_chat_id}"
                else:
                    name = f"#{best_chat_id}"

                _remind_sent[(owner_id, best_chat_id)] = today
                try:
                    await bot.send_message(
                        owner_id,
                        _remind_text(name, best_streak),
                        parse_mode="HTML",
                    )
                    logger.info(
                        "Streak reminder sent owner=%s chat=%s streak=%s",
                        owner_id, best_chat_id, best_streak,
                    )
                except Exception as exc:
                    logger.warning(
                        "Streak reminder DM failed owner=%s chat=%s: %s",
                        owner_id, best_chat_id, exc,
                    )
        except Exception:
            logger.exception("run_reminder_check failed for a batch of owners")
