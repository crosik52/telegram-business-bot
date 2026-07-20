"""Streak notifications sent directly in the business chat with the contact.

Two kinds:
- **Success**: first message of the day from a contact on a ≥3-day streak
  → "🔥 Наша серия N дней продолжается!" — sent INTO the chat via Business API
- **Reminder**: evening loop nudges at-risk streaks — also sent into the chat

Deduplication uses a *pair-scoped* key (min_id, max_id) so that if both users
have the bot connected, only whichever side fires first sends the message.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger

logger = get_logger(__name__)

MIN_STREAK = 3  # minimum streak length before we send any notification

# Pair-scoped dedup: key = (min_id, max_id) → date last notified.
# Same key for (A→B) and (B→A), so only one of them ever sends per day.
_success_sent: dict[tuple[int, int], dt.date] = {}
_remind_sent:  dict[tuple[int, int], dt.date] = {}

_REMIND_HOUR_START = 17
_REMIND_HOUR_END   = 21


def _pk(a: int, b: int) -> tuple[int, int]:
    """Direction-agnostic pair key."""
    return (min(a, b), max(a, b))


# ── DB helpers ────────────────────────────────────────────────────────────────

def _calculate_streak(active_dates: set[dt.date]) -> int:
    """Consecutive-day streak ending today or yesterday."""
    if not active_dates:
        return 0
    today = dt.date.today()
    if max(active_dates) < today - dt.timedelta(days=1):
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
    result = await session.execute(
        select(func.count(Message.id)).where(
            Message.business_connection_id.in_(connection_ids),
            Message.chat_id == chat_id,
            func.date(Message.sent_at) == dt.date.today(),
        )
    )
    return result.scalar_one()


# ── Message text ──────────────────────────────────────────────────────────────

# ── Mutual (both have the bot) — sent into the chat, seen by both sides ───────

def _success_text_mutual(days: int) -> str:
    if days >= 100:
        return f"🏆 <b>Наша серия {days} дней!</b>\nЭто легенда 👑"
    if days >= 30:
        return f"🚀 <b>Наша серия {days} дней</b>\nМесяц и больше — марафон продолжается 💪"
    if days >= 14:
        return f"🔥🔥 <b>Наша серия {days} дней</b>\nДве недели подряд — так держать!"
    if days >= 7:
        return f"🔥 <b>Наша серия {days} дней</b>\nЦелая неделя без пропусков!"
    return f"🔥 <b>Наша серия {days} дней продолжается!</b>\nТак держать 😊"


def _remind_text_mutual(days: int) -> str:
    if days >= 30:
        return (
            f"⏰ <b>Наша серия {days} дней под угрозой!</b>\n"
            f"Напишите сегодня, чтобы не потерять марафон 🏃"
        )
    if days >= 7:
        return (
            f"⏰ <b>Наша серия {days} дней под угрозой</b>\n"
            f"Напишите сегодня, чтобы не прервать 🔥"
        )
    return (
        f"⏰ <b>Наша серия {days} дней под угрозой</b>\n"
        f"Напишите что-нибудь сегодня 😊"
    )


# ── Single (only owner has the bot) — DM to the owner ────────────────────────

def _success_text_dm(name: str, days: int) -> str:
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


def _remind_text_dm(name: str, days: int) -> str:
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


# ── Mutual check helper ───────────────────────────────────────────────────────

async def _is_mutual(session: AsyncSession, contact_id: int) -> bool:
    """Return True if the contact also has an active business connection."""
    from app.models.business_connection import BusinessConnection as BCModel
    row = await session.execute(
        select(BCModel.business_connection_id).where(
            BCModel.user_telegram_id == contact_id,
            BCModel.is_blocked.is_(False),
        ).limit(1)
    )
    return row.scalar_one_or_none() is not None


# ── Success notification ──────────────────────────────────────────────────────

async def maybe_notify_streak_continued(
    bot: Any,
    session: AsyncSession,
    owner_id: int,
    connection_ids: list[str],
    chat_id: int,
    contact_name: str,
) -> None:
    """Send streak-continued notification.

    - Mutual (both have the bot): send into the business chat, deduped by pair key.
    - Single (only owner): send DM to owner.
    """
    today = dt.date.today()
    key   = _pk(owner_id, chat_id)

    if _success_sent.get(key) == today:
        return  # already sent from one side today

    # Only fire on the very first message of today from this chat
    if await _count_today(session, connection_ids, chat_id) != 1:
        return

    dates  = await _get_active_dates(session, connection_ids, chat_id)
    streak = _calculate_streak(dates)
    if streak < MIN_STREAK:
        return

    bc_id  = connection_ids[0] if connection_ids else None
    if not bc_id:
        return

    mutual = await _is_mutual(session, chat_id)

    # Claim the slot before awaiting send to prevent races
    _success_sent[key] = today
    try:
        if mutual:
            await bot.send_message(
                chat_id=chat_id,
                text=_success_text_mutual(streak),
                parse_mode="HTML",
                business_connection_id=bc_id,
            )
            logger.info(
                "Streak success (mutual) sent owner=%s → chat=%s streak=%s",
                owner_id, chat_id, streak,
            )
        else:
            await bot.send_message(
                chat_id=owner_id,
                text=_success_text_dm(contact_name, streak),
                parse_mode="HTML",
            )
            logger.info(
                "Streak success (DM) sent owner=%s chat=%s streak=%s",
                owner_id, chat_id, streak,
            )
    except Exception as exc:
        logger.warning(
            "Streak success failed owner=%s chat=%s: %s", owner_id, chat_id, exc
        )


# ── Reminder loop ─────────────────────────────────────────────────────────────

async def run_reminder_check(bot: Any) -> None:
    """Scan all active users and send at-risk streak reminders in the business chat.

    Runs inside the background loop. Only fires within the evening window (UTC).
    """
    now_utc = dt.datetime.utcnow()
    if not (_REMIND_HOUR_START <= now_utc.hour < _REMIND_HOUR_END):
        return

    today = dt.date.today()

    from app.database.session import get_db_session
    from app.models.business_connection import BusinessConnection as BCModel
    from app.models.message import Message

    async for session in get_db_session():
        try:
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

                best_streak  = 0
                best_chat_id = None

                for chat_id in chat_ids:
                    key = _pk(owner_id, chat_id)
                    if _remind_sent.get(key) == today:
                        continue  # already reminded (from either side)

                    if await _count_today(session, connection_ids, chat_id) > 0:
                        continue  # streak safe today

                    dates  = await _get_active_dates(session, connection_ids, chat_id)
                    streak = _calculate_streak(dates)
                    if streak >= MIN_STREAK and streak > best_streak:
                        best_streak  = streak
                        best_chat_id = chat_id

                if best_chat_id is None:
                    continue

                key = _pk(owner_id, best_chat_id)
                if _remind_sent.get(key) == today:
                    continue  # claimed by the other side between iterations

                bc_id  = connection_ids[0]
                mutual = await _is_mutual(session, best_chat_id)

                # Resolve contact name (needed only for single-owner DM text)
                contact_name = f"#{best_chat_id}"
                if not mutual:
                    from app.models.user import TelegramUser
                    user_row = await session.execute(
                        select(TelegramUser).where(
                            TelegramUser.telegram_user_id == best_chat_id
                        )
                    )
                    user_obj = user_row.scalar_one_or_none()
                    if user_obj:
                        parts = [p for p in [user_obj.first_name, user_obj.last_name] if p]
                        contact_name = " ".join(parts) or contact_name

                _remind_sent[key] = today  # claim before await to prevent races
                try:
                    if mutual:
                        await bot.send_message(
                            chat_id=best_chat_id,
                            text=_remind_text_mutual(best_streak),
                            parse_mode="HTML",
                            business_connection_id=bc_id,
                        )
                        logger.info(
                            "Streak reminder (mutual) sent owner=%s → chat=%s streak=%s",
                            owner_id, best_chat_id, best_streak,
                        )
                    else:
                        await bot.send_message(
                            chat_id=owner_id,
                            text=_remind_text_dm(contact_name, best_streak),
                            parse_mode="HTML",
                        )
                        logger.info(
                            "Streak reminder (DM) sent owner=%s chat=%s streak=%s",
                            owner_id, best_chat_id, best_streak,
                        )
                except Exception as exc:
                    logger.warning(
                        "Streak reminder failed owner=%s chat=%s: %s",
                        owner_id, best_chat_id, exc,
                    )
        except Exception:
            logger.exception("run_reminder_check failed")
