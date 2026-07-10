"""Owner chat commands.

The business-account owner can type ``!command`` in any connected chat.
The bot will:

1. Execute the command (query DB, update settings, etc.).
2. Send the result as a private DM to the owner — invisible to the contact.
3. Best-effort delete the ``!command`` message from the chat so the
   contact never sees it.

Available commands
------------------
!help            — list all commands
!info            — statistics about this contact
!note <text>     — save a note about this contact
!notes           — show all notes for this contact
!mute <30m|2h|1d> — mute notifications from this chat
!unmute          — re-enable notifications
"""

from __future__ import annotations

import datetime as dt
import re

from aiogram import Bot
from aiogram.methods import DeleteMessage
from aiogram.types import BufferedInputFile
from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.message import Message
from app.repositories.chat_settings_repository import ChatSettingsRepository
from app.repositories.contact_note_repository import ContactNoteRepository
from app.services.chart_service import InfoStats, render_info_image

logger = get_logger(__name__)

# ── Regex helpers ─────────────────────────────────────────────────────────────

_COMMAND_RE = re.compile(r"^!(\w+)(?:\s+([\s\S]*))?$")
_DURATION_RE = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)

# ── Duration parser ───────────────────────────────────────────────────────────

_UNIT_MAP = {"m": "minutes", "h": "hours", "d": "days"}


def _parse_duration(s: str) -> dt.timedelta | None:
    m = _DURATION_RE.match(s.strip())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    return dt.timedelta(**{_UNIT_MAP[unit]: n})


# ── Low-level helpers ─────────────────────────────────────────────────────────

async def _reply(bot: Bot, owner_id: int, text: str) -> None:
    """Send command feedback to the owner via DM."""
    try:
        await bot.send_message(chat_id=owner_id, text=text, parse_mode="HTML")
    except Exception:
        logger.exception("Failed to send command reply to owner %s", owner_id)


async def _delete_cmd_msg(
    bot: Bot, chat_id: int, message_id: int, business_connection_id: str
) -> None:
    """Best-effort deletion of the owner's !command from the business chat.

    Uses the raw ``DeleteMessage`` method with ``business_connection_id`` so
    the deletion is performed on behalf of the connected account.  Failures
    are logged at DEBUG level because not all bot API versions / connection
    types support this.
    """
    try:
        await bot(
            DeleteMessage(
                chat_id=chat_id,
                message_id=message_id,
                business_connection_id=business_connection_id,
            )
        )
    except Exception:
        logger.debug(
            "Could not delete command message_id=%s from chat_id=%s "
            "(business_connection_id=%s); contact may have seen it.",
            message_id,
            chat_id,
            business_connection_id,
        )


# ── Command implementations ───────────────────────────────────────────────────

_HELP_TEXT = (
    "📋 <b>Доступные команды</b> (пишите прямо в чате):\n\n"
    "<code>!info</code> — статистика по собеседнику\n"
    "<code>!note текст</code> — сохранить заметку\n"
    "<code>!notes</code> — показать все заметки\n"
    "<code>!mute 30m</code> / <code>2h</code> / <code>1d</code> — "
    "отключить уведомления из этого чата\n"
    "<code>!unmute</code> — включить уведомления обратно\n"
    "<code>!help</code> — эта справка\n"
)


async def _cmd_help(*, bot: Bot, owner_id: int, **_: object) -> None:
    await _reply(bot, owner_id, _HELP_TEXT)


async def _cmd_info(
    *,
    bot: Bot,
    owner_id: int,
    chat_id: int,
    business_connection_id: str,
    session: AsyncSession,
    **_: object,
) -> None:
    base = [
        Message.business_connection_id == business_connection_id,
        Message.chat_id == chat_id,
    ]
    now = dt.datetime.now(dt.UTC)

    # --- Aggregate counts in one query ---
    _MEDIA_TYPES = ("photo", "video", "document", "sticker", "animation",
                    "video_note", "contact", "location", "poll", "other")
    _AUDIO_TYPES = ("voice", "audio")

    agg = (
        await session.execute(
            select(
                func.count().label("total"),
                func.sum(case((Message.is_deleted.is_(True), 1), else_=0)).label("deleted"),
                func.sum(case((Message.is_edited.is_(True), 1), else_=0)).label("edited"),
                func.sum(case((Message.sender_telegram_id != owner_id, 1), else_=0)).label("incoming"),
                func.sum(case((Message.sender_telegram_id == owner_id, 1), else_=0)).label("outgoing"),
                func.sum(case((Message.media_type.in_(_MEDIA_TYPES), 1), else_=0)).label("media_count"),
                func.sum(case((Message.media_type.in_(_AUDIO_TYPES), 1), else_=0)).label("audio_count"),
                func.min(Message.sent_at).label("first_seen"),
                func.max(Message.sent_at).label("last_seen"),
            ).select_from(Message).where(*base)
        )
    ).one()

    total       = agg.total       or 0
    deleted     = agg.deleted     or 0
    edited      = agg.edited      or 0
    incoming    = agg.incoming    or 0
    outgoing    = agg.outgoing    or 0
    media_count = agg.media_count or 0
    audio_count = agg.audio_count or 0

    # --- Daily breakdown (last 30 days, non-deleted) ---
    thirty_days_ago = now - dt.timedelta(days=30)
    day_col = cast(Message.sent_at, Date).label("day")
    daily_rows = (
        await session.execute(
            select(
                day_col,
                func.sum(case((Message.sender_telegram_id != owner_id, 1), else_=0)).label("inbound"),
                func.sum(case((Message.sender_telegram_id == owner_id, 1), else_=0)).label("outbound"),
            )
            .where(
                *base,
                Message.sent_at >= thirty_days_ago,
                Message.is_deleted.is_(False),
            )
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()

    def _day_label(val: object) -> str:
        """Format a date value that may arrive as date, datetime, or ISO string."""
        if isinstance(val, dt.datetime):
            return val.strftime("%d.%m")
        if isinstance(val, dt.date):
            return val.strftime("%d.%m")
        # SQLite returns ISO string "YYYY-MM-DD"
        return dt.date.fromisoformat(str(val)[:10]).strftime("%d.%m")

    daily: list[tuple[str, int, int]] = [
        (_day_label(r.day), int(r.inbound or 0), int(r.outbound or 0))
        for r in daily_rows
    ]

    # --- Contact display name (most recent message from the interlocutor) ---
    name_row = (
        await session.execute(
            select(
                Message.sender_first_name,
                Message.sender_last_name,
                Message.sender_username,
            )
            .where(
                *base,
                Message.sender_telegram_id != owner_id,
                Message.sender_telegram_id.is_not(None),
                Message.is_deleted.is_(False),
            )
            .order_by(Message.sent_at.desc())
            .limit(1)
        )
    ).first()

    if name_row:
        parts = [p for p in (name_row[0], name_row[1]) if p]
        contact_name = " ".join(parts) if parts else (
            f"@{name_row[2]}" if name_row[2] else f"Собеседник {chat_id}"
        )
    else:
        contact_name = f"Собеседник {chat_id}"

    # --- Mute status + notes ---
    chat_repo = ChatSettingsRepository(session)
    settings  = await chat_repo.get(business_connection_id, chat_id)
    muted_until = (
        settings.muted_until
        if settings and settings.muted_until and settings.muted_until > now
        else None
    )

    note_repo  = ContactNoteRepository(session)
    note_count = len(await note_repo.get_for_chat(business_connection_id, chat_id))

    # --- Render image ---
    info = InfoStats(
        contact_name=contact_name,
        total=total,
        incoming=incoming,
        outgoing=outgoing,
        deleted=deleted,
        edited=edited,
        media_count=media_count,
        audio_count=audio_count,
        first_seen=agg.first_seen,
        last_seen=agg.last_seen,
        note_count=note_count,
        muted_until=muted_until,
        daily=daily,
    )

    try:
        buf = render_info_image(info)
        photo = BufferedInputFile(buf.getvalue(), filename="stats.png")
        await bot.send_photo(chat_id=owner_id, photo=photo)
    except Exception:
        logger.exception("Failed to render !info image; falling back to text")
        del_pct   = round(deleted / total * 100) if total else 0
        first_str = agg.first_seen.strftime("%d.%m.%Y") if agg.first_seen else "—"
        last_str  = agg.last_seen.strftime("%d.%m %H:%M")  if agg.last_seen  else "—"
        notes_line = f"\n📝 Заметок: {note_count}" if note_count else ""
        muted_line = (
            f"\n🔕 до {muted_until.strftime('%d.%m %H:%M UTC')}" if muted_until else ""
        )
        await _reply(
            bot, owner_id,
            "📊 <b>Статистика чата</b>\n\n"
            f"💬 Сообщений: <b>{total}</b>\n"
            f"🗑 Удалено: <b>{deleted}</b> ({del_pct}%)\n"
            f"✏️ Отредактировано: <b>{edited}</b>\n"
            f"📅 Первое: {first_str}\n"
            f"🕐 Последнее: {last_str}"
            f"{notes_line}{muted_line}\n\n"
            "<i>Учитываются только сообщения с момента подключения бота.</i>",
        )


async def _cmd_note(
    *,
    bot: Bot,
    owner_id: int,
    chat_id: int,
    business_connection_id: str,
    session: AsyncSession,
    args: str | None,
    **_: object,
) -> None:
    if not args or not args.strip():
        await _reply(
            bot, owner_id,
            "❌ Укажите текст: <code>!note текст заметки</code>",
        )
        return
    repo = ContactNoteRepository(session)
    note = await repo.add(business_connection_id, chat_id, args.strip())
    await _reply(bot, owner_id, f"✅ <b>Заметка сохранена:</b>\n\n«{note.text}»")


async def _cmd_notes(
    *,
    bot: Bot,
    owner_id: int,
    chat_id: int,
    business_connection_id: str,
    session: AsyncSession,
    **_: object,
) -> None:
    repo = ContactNoteRepository(session)
    notes = await repo.get_for_chat(business_connection_id, chat_id)
    if not notes:
        await _reply(bot, owner_id, "📝 Нет заметок по этому собеседнику.")
        return
    header = f"📝 <b>Заметки ({len(notes)})</b>\n"
    lines = [
        f"{i}. [{n.created_at.strftime('%d.%m %H:%M')}] {n.text}"
        for i, n in enumerate(notes, 1)
    ]
    await _reply(bot, owner_id, header + "\n".join(lines))


async def _cmd_mute(
    *,
    bot: Bot,
    owner_id: int,
    chat_id: int,
    business_connection_id: str,
    session: AsyncSession,
    args: str | None,
    **_: object,
) -> None:
    if not args or not args.strip():
        await _reply(
            bot, owner_id,
            "❌ Укажите длительность: <code>!mute 30m</code> / <code>2h</code> / <code>1d</code>",
        )
        return
    delta = _parse_duration(args.strip())
    if delta is None:
        await _reply(bot, owner_id, "❌ Неверный формат. Примеры: <code>30m</code>, <code>2h</code>, <code>1d</code>")
        return
    until = dt.datetime.now(dt.UTC) + delta
    repo = ChatSettingsRepository(session)
    await repo.set_muted_until(business_connection_id, chat_id, until)
    await _reply(
        bot, owner_id,
        f"🔕 <b>Уведомления отключены</b> до {until.strftime('%d.%m.%Y %H:%M UTC')}",
    )


async def _cmd_unmute(
    *,
    bot: Bot,
    owner_id: int,
    chat_id: int,
    business_connection_id: str,
    session: AsyncSession,
    **_: object,
) -> None:
    repo = ChatSettingsRepository(session)
    await repo.set_muted_until(business_connection_id, chat_id, None)
    await _reply(bot, owner_id, "🔔 <b>Уведомления включены</b>")


# ── Dispatch table ────────────────────────────────────────────────────────────

_HANDLERS: dict[str, object] = {
    "help":   _cmd_help,
    "info":   _cmd_info,
    "note":   _cmd_note,
    "notes":  _cmd_notes,
    "mute":   _cmd_mute,
    "unmute": _cmd_unmute,
}


# ── Public API ────────────────────────────────────────────────────────────────

def parse_command(text: str | None) -> tuple[str, str | None] | None:
    """Return ``(command, args_or_None)`` if *text* starts with ``!``, else None."""
    if not text:
        return None
    m = _COMMAND_RE.match(text.strip())
    if not m:
        return None
    return m.group(1).lower(), m.group(2)


async def dispatch(
    cmd: str,
    args: str | None,
    *,
    bot: Bot,
    owner_id: int,
    chat_id: int,
    business_connection_id: str,
    message_id: int,
    session: AsyncSession,
) -> None:
    """Route a parsed command to its handler and then delete the command message."""
    handler = _HANDLERS.get(cmd)
    if handler is None:
        await _reply(
            bot, owner_id,
            f"❓ Неизвестная команда <code>!{cmd}</code>. "
            "Введите <code>!help</code> для справки.",
        )
    else:
        await handler(  # type: ignore[operator]
            bot=bot,
            owner_id=owner_id,
            chat_id=chat_id,
            business_connection_id=business_connection_id,
            session=session,
            args=args,
        )

    # Always attempt to hide the command message from the contact.
    await _delete_cmd_msg(bot, chat_id, message_id, business_connection_id)
