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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.message import Message
from app.repositories.chat_settings_repository import ChatSettingsRepository
from app.repositories.contact_note_repository import ContactNoteRepository

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
    # --- Message counts ---
    base = [
        Message.business_connection_id == business_connection_id,
        Message.chat_id == chat_id,
    ]
    total = (
        await session.execute(
            select(func.count()).select_from(Message).where(*base)
        )
    ).scalar_one()
    deleted = (
        await session.execute(
            select(func.count())
            .select_from(Message)
            .where(*base, Message.is_deleted.is_(True))
        )
    ).scalar_one()
    edited = (
        await session.execute(
            select(func.count())
            .select_from(Message)
            .where(*base, Message.is_edited.is_(True))
        )
    ).scalar_one()
    first_seen = (
        await session.execute(
            select(func.min(Message.sent_at)).where(*base)
        )
    ).scalar_one()
    last_seen = (
        await session.execute(
            select(func.max(Message.sent_at)).where(*base)
        )
    ).scalar_one()

    del_pct = round(deleted / total * 100) if total else 0

    # --- Mute status ---
    chat_repo = ChatSettingsRepository(session)
    settings = await chat_repo.get(business_connection_id, chat_id)
    muted_line = ""
    if settings and settings.muted_until and settings.muted_until > dt.datetime.now(dt.UTC):
        until_str = settings.muted_until.strftime("%d.%m %H:%M UTC")
        muted_line = f"\n🔕 Уведомления отключены до {until_str}"

    # --- Notes count ---
    note_repo = ContactNoteRepository(session)
    note_count = len(await note_repo.get_for_chat(business_connection_id, chat_id))
    notes_line = f"\n📝 Заметок: {note_count}" if note_count else ""

    first_str = first_seen.strftime("%d.%m.%Y") if first_seen else "—"
    last_str = last_seen.strftime("%d.%m %H:%M") if last_seen else "—"

    text = (
        "📊 <b>Статистика чата</b>\n\n"
        f"💬 Сообщений: <b>{total}</b>\n"
        f"🗑 Удалено: <b>{deleted}</b> ({del_pct}%)\n"
        f"✏️ Отредактировано: <b>{edited}</b>\n"
        f"📅 Первое: {first_str}\n"
        f"🕐 Последнее: {last_str}"
        f"{notes_line}"
        f"{muted_line}"
        "\n\n<i>Учитываются только сообщения с момента подключения бота.</i>"
    )
    await _reply(bot, owner_id, text)


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
