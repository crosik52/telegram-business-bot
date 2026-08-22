"""aiogram handlers for Telegram Business updates.

Covers every officially supported Business API update type:

- ``business_connection``        -> connection lifecycle (created/updated/revoked)
- ``business_message``           -> new incoming/outgoing message in a connected chat
- ``edited_business_message``    -> a business message was edited
- ``deleted_business_messages``  -> one or more business messages were deleted

Owner commands
--------------
If the *owner* (the account that connected the bot) types a message beginning
with ``!`` in a business chat, the bot treats it as a command, executes it,
DMs the result back to the owner, and attempts to delete the command message
so the contact never sees it.  See ``app.business.commands`` for the full
command reference.

Panic-delete detection
----------------------
When a contact deletes 3 or more messages in a single event (simultaneously)
the bot sends ONE grouped ⚠️ notification instead of N individual ones.
Rapid sequential deletions across events are also tracked; a cross-event panic
alert fires once the rolling 60-second window crosses the threshold.

Telegram Business API limitation (documented, not worked around):
Telegram does NOT send deleted-message content in ``deleted_business_messages``
updates — only chat_id + message_ids.  This bot therefore relies entirely on
having captured each message via ``business_message`` first.
"""

from __future__ import annotations

import asyncio
from html import escape as html_escape

import shutil
import tempfile

from aiogram import Bot, F, Router
from aiogram.types import (
    BufferedInputFile, BusinessConnection, BusinessMessagesDeleted,
    CallbackQuery, ChosenInlineResult, FSInputFile, InlineQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultAudio, InlineQueryResultCachedAudio,
    InputMediaAudio, InputTextMessageContent,
    Message, PreCheckoutQuery, WebAppInfo,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import emoji as E
from app.business import commands
from app.business.panic_tracker import PanicTracker
from app.config import get_settings
from app.database.session import session_scope
from app.logging_config import get_logger
from app.models.business_connection import BusinessConnection as BCModel
from app.models.message import MediaType, Message as DBMessage
from app.repositories.chat_settings_repository import ChatSettingsRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.business import permissions
from app.services import audio_service, media_cache_service
from app.services import ai_analysis_service
from app.services.message_service import MessageService
from app.services.video_service import extract_video_url, handle_video_link

# Strong references to background download tasks — prevents GC before completion.
_download_tasks: set[asyncio.Task] = set()


def _miniapp_kb(label: str = "📊 Открыть мини-приложение", path: str = "/app") -> InlineKeyboardMarkup | None:
    """Return a one-button keyboard that opens the mini-app, or None if no URL is configured."""
    base = get_settings().webhook_base_url
    if not base:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, web_app=WebAppInfo(url=base.rstrip("/") + path))
    ]])

# Global semaphore: at most 3 video downloads run concurrently across all chats.
_download_semaphore = asyncio.Semaphore(3)


# ── In-memory cache: connection_id → (owner_telegram_id, can_reply) ──────────
# Eliminates one SELECT per incoming message — connections rarely change.
# Invalidated / updated on every business_connection lifecycle event.
_bc_cache: dict[str, tuple[int, bool]] = {}

# Conversation-level dedup: prevents both bots from downloading the same link
# when both participants have the bot connected.
# Key: (min(owner_id, chat_id), max(owner_id, chat_id), url) — symmetric,
# so A↔B and B↔A produce the same key regardless of who detected the link.
_in_flight: set[tuple[int, int, str]] = set()  # (lo_id, hi_id, url)

logger = get_logger(__name__)
router = Router(name="business")

# One shared panic tracker for the lifetime of the process.
_panic_tracker = PanicTracker()

_PREVIEW_LIMIT = 500

# Human-readable labels for media types shown in notifications.
_MEDIA_LABELS: dict[MediaType, str] = {
    MediaType.PHOTO:      "фото",
    MediaType.VIDEO:      "видео",
    MediaType.VOICE:      "голосовое сообщение",
    MediaType.VIDEO_NOTE: "видеосообщение (кружок)",
    MediaType.AUDIO:      "аудио",
    MediaType.DOCUMENT:   "документ",
    MediaType.STICKER:    "стикер",
    MediaType.ANIMATION:  "анимация (GIF)",
    MediaType.CONTACT:    "контакт",
    MediaType.LOCATION:   "геолокация",
    MediaType.POLL:       "опрос",
}

# Panic threshold is intentionally the same as PanicTracker.THRESHOLD so both
# the bulk-event check and the cross-event check use a consistent value.
_PANIC_THRESHOLD = 3


# ── Small helpers ─────────────────────────────────────────────────────────────

def _preview(text: str | None) -> str:
    if not text:
        return ""
    trimmed = text if len(text) <= _PREVIEW_LIMIT else text[:_PREVIEW_LIMIT] + "…"
    return html_escape(trimmed)


def _media_label(media_type: MediaType) -> str:
    return _MEDIA_LABELS.get(media_type, "медиа")


def _get_file_info(message: Message) -> tuple[str | None, str | None, str | None]:
    """Return (file_id, file_unique_id, media_type_str) for a message's media."""
    if message.photo:
        p = message.photo[-1]
        return p.file_id, p.file_unique_id, "photo"
    if message.video:
        return message.video.file_id, message.video.file_unique_id, "video"
    if message.voice:
        return message.voice.file_id, message.voice.file_unique_id, "voice"
    if message.video_note:
        return message.video_note.file_id, message.video_note.file_unique_id, "video_note"
    if message.audio:
        return message.audio.file_id, message.audio.file_unique_id, "audio"
    if message.document:
        return message.document.file_id, message.document.file_unique_id, "document"
    if message.sticker:
        return message.sticker.file_id, message.sticker.file_unique_id, "sticker"
    if message.animation:
        return message.animation.file_id, message.animation.file_unique_id, "animation"
    return None, None, None


_NO_FILE_TYPES = (
    MediaType.NONE, MediaType.TEXT, MediaType.CONTACT,
    MediaType.LOCATION, MediaType.POLL, MediaType.OTHER,
)


async def _handle_dot_save(
    bot: Bot,
    owner_id: int,
    business_connection_id: str,
    chat_id: int,
    reply_to_message_id: int,
    dot_message_id: int,
) -> None:
    """Forward the replied-to media to the owner's DM on any reply.

    Strategy:
    1. Look up the original message in DB.
    2. Use already-cached bytes if available.
    3. If not cached, try to download right now (file may still be valid
       at reply time even if the background task failed or timed out).
    4. Send to owner's DM.
    5. Notify owner on failure so they're not left wondering.
    """
    logger.info(
        "dot-save: triggered owner=%s chat=%s reply_to=%s",
        owner_id, chat_id, reply_to_message_id,
    )
    try:
        async with session_scope() as session:
            result = await session.execute(
                select(DBMessage).where(
                    DBMessage.chat_id == chat_id,
                    DBMessage.message_id == reply_to_message_id,
                )
            )
            ref = result.scalar_one_or_none()

            if ref is None:
                logger.info(
                    "dot-save: reply_to=%s not in DB (chat=%s) — skipping silently "
                    "(bot-sent video or message received while offline)",
                    reply_to_message_id, chat_id,
                )
                return

            if ref.media_type in _NO_FILE_TYPES:
                # Genuine non-media message (text, poll, contact…) — nothing to save.
                logger.info(
                    "dot-save: message %s is non-media (type=%s) — skipping",
                    reply_to_message_id, ref.media_type,
                )
                return

            if not ref.file_id:
                await bot.send_message(
                    chat_id=owner_id,
                    text=(
                        f"{E.WARNING} <b>Истекшее медиа</b> — "
                        "файл уже удалён с серверов Telegram и недоступен."
                    ),
                    parse_mode="HTML",
                )
                return

            logger.info(
                "dot-save: found %s file_id=%.20s…",
                ref.media_type.value, ref.file_id,
            )

            # ── Self-destructing detection ────────────────────────────────────
            #
            # The ONLY reliable signal is whether the Bot API can download the
            # file right now:
            #   • Download succeeds  →  regular media (still on Telegram CDN)
            #                          →  owner just replied casually, ignore it
            #   • Download fails     →  self-destructing / view-once / expired
            #                          →  try cached bytes then Telethon, notify
            #
            # We intentionally do NOT forward regular media even if the owner
            # replied to it — only view-once media should arrive in their DM.

            caption = ref.caption or ref.text or None

            # ── Check: is it still downloadable via Bot API? ──────────────────
            fresh_ok = await media_cache_service.download_and_cache(
                bot, session, ref.file_id, ref.file_unique_id, ref.media_type.value
            )
            if fresh_ok:
                # Regular accessible media — owner replied to a normal message.
                # Do nothing (no DM, no notification).
                logger.info(
                    "dot-save: %s is regular media (bot_api ok) — skipping silently",
                    ref.media_type.value,
                )
                return

            # ── Bot API blocked → treat as self-destructing ───────────────────
            logger.info(
                "dot-save: bot_api blocked %s — self-destructing, attempting recovery",
                ref.media_type.value,
            )

            # Sub-tier A: bytes already cached at message-receipt time
            cached_bytes = await media_cache_service.get_cached_bytes(
                session, ref.file_unique_id
            )
            if cached_bytes:
                logger.info("dot-save: serving %s from cache", ref.media_type.value)
                await _try_send_media(
                    bot, owner_id, ref.media_type, ref.file_id,
                    file_unique_id=ref.file_unique_id,
                    session=session,
                    caption=caption,
                )
                return

            # ── All tiers failed ──────────────────────────────────────────────
            logger.warning(
                "dot-save: ✗ all methods failed for %s owner=%s (chat=%s msg=%s)",
                ref.media_type.value, owner_id, ref.chat_id, ref.message_id,
            )
            await bot.send_message(
                chat_id=owner_id,
                text=(
                    f"{E.WARNING} Не удалось сохранить медиа — "
                    "файл уже недоступен (самоудалился или защищён)."
                ),
                parse_mode="HTML",
            )

    except Exception:
        logger.exception(
            "dot-save: unexpected error reply_to=%s chat=%s",
            reply_to_message_id, chat_id,
        )

    # No deletion — the reply is a genuine message to the contact.


async def _cache_media_in_background(
    bot: Bot,
    file_id: str,
    file_unique_id: str,
    media_type_str: str,
    *,
    owner_id: int | None = None,
    is_self_destructing: bool = False,
    partner_name: str = "собеседника",
) -> None:
    """Background task: download and cache media bytes immediately after receipt.

    Self-destructing media file_ids expire within seconds of the message
    disappearing.  By caching here — synchronously with message ingestion —
    the bytes are always available when a delete notification fires later.

    When *is_self_destructing* is True (message.has_media_spoiler or detected
    at receipt time) the task also proactively notifies the owner:
      • Success → forwards the cached file to the owner's DM.
      • Failure → tells the owner the file could not be captured.
    """
    try:
        async with session_scope() as session:
            cached = await media_cache_service.download_and_cache(
                bot, session, file_id, file_unique_id, media_type_str
            )
            if cached:
                await session.commit()
                if is_self_destructing and owner_id:
                    # Proactively deliver the captured file to the owner's DM
                    # so they don't have to use dot-save and can't miss it.
                    media_type_enum = MediaType(media_type_str)
                    sent = await _try_send_media(
                        bot, owner_id, media_type_enum, file_id,
                        file_unique_id=file_unique_id,
                        session=session,
                        caption=f"📥 Самоудаляющееся медиа от {partner_name}",
                    )
                    if not sent:
                        logger.warning(
                            "Captured self-destructing %s but failed to forward "
                            "to owner=%s", media_type_str, owner_id,
                        )
            elif is_self_destructing and owner_id:
                # File was already gone / protected before we could cache it.
                try:
                    await bot.send_message(
                        owner_id,
                        f"🚫 <b>Самоудаляющееся медиа</b> от {partner_name} — "
                        f"файл исчез до того, как бот успел его сохранить.",
                        parse_mode="HTML",
                    )
                except Exception as _notify_exc:
                    logger.warning(
                        "Could not notify owner=%s about missed self-destructing media: %s",
                        owner_id, _notify_exc,
                    )
    except Exception:
        logger.warning(
            "Background media cache task failed for file_unique_id=%s", file_unique_id
        )


def _counterpart_label(chat: object, owner_telegram_id: int) -> str:
    """Human-readable label for the other side of the chat."""
    if getattr(chat, "id", None) == owner_telegram_id:
        return "себя"
    parts = [p for p in (getattr(chat, "first_name", None), getattr(chat, "last_name", None)) if p]
    name = " ".join(parts) or (getattr(chat, "title", None) or "собеседником")
    label = html_escape(name)
    username = getattr(chat, "username", None)
    if username:
        label += f" (@{html_escape(username)})"
    return label


async def _try_send_media(
    bot: Bot,
    chat_id: int,
    media_type: MediaType,
    file_id: str | None,
    *,
    file_unique_id: str | None = None,
    session: AsyncSession | None = None,
    caption: str | None = None,
) -> bool:
    """Resend a Telegram media file to *chat_id*.

    Strategy (in order):
    1. Use cached bytes from DB (works even after file_id expiry — self-destructing media).
    2. Fall back to the stored file_id if no cache entry exists.

    Returns True on success, False on failure.
    Callers should fall back gracefully — the text notification has already
    been sent.
    """
    kw: dict = {"chat_id": chat_id}
    if caption:
        kw["caption"] = caption
    kw_no_caption: dict = {"chat_id": chat_id}

    # ── 1. Try cached bytes ───────────────────────────────────────────────────
    cached_bytes: bytes | None = None
    if session is not None and file_unique_id:
        cached_bytes = await media_cache_service.get_cached_bytes(session, file_unique_id)

    def _media(type_str: str) -> "BufferedInputFile | str":
        if cached_bytes is not None:
            return media_cache_service.make_input_file(cached_bytes, type_str)
        return file_id or ""

    # ── 2. Send ───────────────────────────────────────────────────────────────
    try:
        match media_type:
            case MediaType.PHOTO:
                await bot.send_photo(photo=_media("photo"), **kw)
            case MediaType.VIDEO:
                await bot.send_video(video=_media("video"), **kw)
            case MediaType.VOICE:
                await bot.send_voice(voice=_media("voice"), **kw)
            case MediaType.VIDEO_NOTE:
                await bot.send_video_note(video_note=_media("video_note"), **kw_no_caption)
            case MediaType.AUDIO:
                await bot.send_audio(audio=_media("audio"), **kw)
            case MediaType.DOCUMENT:
                await bot.send_document(document=_media("document"), **kw)
            case MediaType.STICKER:
                await bot.send_sticker(sticker=_media("sticker"), **kw_no_caption)
            case MediaType.ANIMATION:
                await bot.send_animation(animation=_media("animation"), **kw)
            case _:
                # CONTACT, LOCATION, POLL etc. have no file_id; skip silently.
                return False
        return True
    except Exception:
        logger.warning(
            "Failed to resend media type=%s to chat_id=%s (cached=%s, file_id=%s)",
            media_type.value,
            chat_id,
            cached_bytes is not None,
            bool(file_id),
        )
        return False


# ── Notification builders ─────────────────────────────────────────────────────

async def _send_single_delete_notification(
    bot: Bot,
    owner_id: int,
    counterpart: str,
    removed: DBMessage,
) -> None:
    """Send one delete notification and try to forward any cached media.

    Media recovery order:
      1. Cached bytes from DB (stored at message-receipt time).
      2. Telethon MTProto download (if still accessible — i.e. message just
         self-destructed and Telethon user-client is configured).
    View-once messages have media_type set but file_id=None; we handle them
    correctly by checking media_type instead of file_id.
    """
    # A message is "media" if its type implies a downloadable file, even when
    # the file_id is absent (view-once stored without accessible file_id).
    has_media = removed.media_type not in _NO_FILE_TYPES
    media_lbl = _media_label(removed.media_type) if has_media else None
    text_part = _preview(removed.text or removed.caption)

    if has_media and text_part:
        notification = f"{E.TRASH} {counterpart} удалил(а) {media_lbl}:\n\n«{text_part}»"
    elif has_media:
        notification = f"{E.TRASH} {counterpart} удалил(а) {media_lbl}."
    elif text_part:
        notification = f"{E.TRASH} {counterpart} удалил(а) сообщение:\n\n«{text_part}»"
    else:
        notification = f"{E.TRASH} {counterpart} удалил(а) сообщение."

    try:
        await bot.send_message(chat_id=owner_id, text=notification, parse_mode="HTML")

        if not has_media:
            return

        async with session_scope() as session:
            # ── Tier 1: cached bytes (downloaded at arrival time) ─────────────
            sent = await _try_send_media(
                bot, owner_id, removed.media_type, removed.file_id,
                file_unique_id=removed.file_unique_id,
                session=session,
                caption=text_part or None,
            )
            if sent:
                return


    except Exception:
        logger.exception(
            "Failed to send delete notification to owner %s", owner_id
        )


async def _send_panic_bulk(
    bot: Bot,
    owner_id: int,
    counterpart: str,
    messages: list[DBMessage],
) -> None:
    """One grouped panic notification for a simultaneous bulk-delete event."""
    n = len(messages)
    lines: list[str] = [
        f"{E.WARNING} <b>Паник-удаление!</b>\n\n"
        f"{counterpart} удалил(а) <b>{n} сообщений</b> разом:\n"
    ]
    # (media_type, file_id, caption, file_unique_id)
    media_to_resend: list[tuple[MediaType, str | None, str | None, str | None]] = []

    for i, msg in enumerate(messages, 1):
        has_media = msg.file_id is not None
        text_part = _preview(msg.text or msg.caption)

        if has_media and text_part:
            lines.append(f"{i}. {_media_label(msg.media_type)} — «{text_part}»")
        elif has_media:
            lines.append(f"{i}. {_media_label(msg.media_type)}")
        elif text_part:
            lines.append(f"{i}. «{text_part}»")
        else:
            lines.append(f"{i}. <i>(нет содержимого)</i>")

        if has_media:
            media_to_resend.append(
                (msg.media_type, msg.file_id, text_part or None, msg.file_unique_id)
            )

    try:
        await bot.send_message(
            chat_id=owner_id, text="\n".join(lines), parse_mode="HTML"
        )
        if media_to_resend:
            async with session_scope() as session:
                for media_type, file_id, caption, file_uq_id in media_to_resend:
                    await _try_send_media(
                        bot, owner_id, media_type, file_id,
                        file_unique_id=file_uq_id,
                        session=session,
                        caption=caption,
                    )
    except Exception:
        logger.exception(
            "Failed to send panic-bulk notification to owner %s", owner_id
        )


async def _send_cross_event_panic(
    bot: Bot,
    owner_id: int,
    counterpart: str,
    total: int,
) -> None:
    """Additional alert when rapid sequential deletions cross the threshold."""
    text = (
        f"{E.WARNING} <b>Паник-детект:</b> {counterpart} удалил(а) уже "
        f"<b>{total} сообщений</b> за последнюю минуту."
    )
    try:
        await bot.send_message(chat_id=owner_id, text=text, parse_mode="HTML")
    except Exception:
        logger.exception(
            "Failed to send cross-event panic alert to owner %s", owner_id
        )


# ── Connection handler ────────────────────────────────────────────────────────

@router.business_connection()
async def on_business_connection(connection: BusinessConnection) -> None:
    """Persist the lifecycle of a Telegram Business connection."""

    # Derive the bot's own Telegram user ID from the token (format: <id>:<secret>).
    # Stored on every connection so the admin panel can separate old-bot vs new-bot users.
    _settings = get_settings()
    try:
        _current_bot_id = int(_settings.telegram_bot_token.split(":")[0])
    except Exception:
        _current_bot_id = None

    async with session_scope() as session:
        result = await session.execute(
            select(BCModel).where(BCModel.business_connection_id == connection.id)
        )
        record = result.scalar_one_or_none()

        # Track whether this connection was previously known and active —
        # we only send a disconnect notification in that case.
        was_previously_active = record is not None and record.is_enabled

        if record is None:
            record = BCModel(
                business_connection_id=connection.id,
                user_telegram_id=connection.user.id,
                user_first_name=connection.user.first_name,
                user_last_name=connection.user.last_name,
                user_username=connection.user.username,
                can_reply=connection.can_reply,
                is_enabled=connection.is_enabled,
                bot_id=_current_bot_id,
            )
            session.add(record)
        else:
            record.can_reply = connection.can_reply
            record.is_enabled = connection.is_enabled
            record.user_first_name = connection.user.first_name
            record.user_last_name = connection.user.last_name
            record.user_username = connection.user.username
            # Update bot_id so a re-connect through the new bot is reflected.
            if _current_bot_id is not None:
                record.bot_id = _current_bot_id

        # When a connection is disabled/revoked, wipe all per-chat panic state
        # for that connection.  Keys use the format "connection_id:chat_id", so
        # we match by prefix rather than an exact key.
        if not connection.is_enabled:
            keys_to_clear = [
                k for k in list(_panic_tracker._state)
                if k.startswith(f"{connection.id}:")
            ]
            for k in keys_to_clear:
                _panic_tracker.clear(k)
            _bc_cache.pop(connection.id, None)
            # Invalidate all cached analyses for this owner — the connection is
            # gone so every cached result is now stale / unauthorised.
            await ai_analysis_service.invalidate_cache_for_owner(
                session, connection.user.id
            )
            # Notify the user only if this connection was previously active.
            # Telegram can send is_enabled=False on the very first event when
            # the user switches bots — in that case there's nothing to notify.
            if not was_previously_active:
                return
            try:
                settings = get_settings()
                base_url = (settings.webhook_base_url or "").rstrip("/")
                reconnect_kb = None
                if base_url:
                    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo  # noqa: PLC0415
                    reconnect_kb = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text="📊 Открыть статистику",
                            web_app=WebAppInfo(url=base_url + "/app"),
                        )
                    ]])
                from app.business.dispatcher import get_bot as _get_bot  # noqa: PLC0415
                _main_bot = _get_bot(get_settings())
                await _main_bot.send_message(
                    connection.user.id,
                    "🔌 <b>Бот отключён от твоего аккаунта</b>\n\n"
                    "Ты отключил бота от аккаунта — статистика больше не обновляется, "
                    "уведомления об удалениях не приходят, питомцы и отношения перестанут обновляться.\n\n"
                    "Все данные, монеты и статистика сохранены — ничего не потеряется 🔒",
                    parse_mode="HTML",
                    reply_markup=reconnect_kb,
                )
            except Exception as _exc:
                logger.warning(
                    "Could not send disconnect notification to user %s: %s",
                    connection.user.id, _exc,
                )
        else:
            _bc_cache[connection.id] = (connection.user.id, connection.can_reply)

    logger.info(
        "Business connection %s enabled=%s can_reply=%s",
        connection.id, connection.is_enabled, connection.can_reply,
    )


# ── New message handler ───────────────────────────────────────────────────────

_fetching_avatars: set[int] = set()  # debounce concurrent avatar fetches per user


async def _try_fetch_avatar(bot: Bot, user_id: int) -> None:
    """Background task: fetch Telegram profile photo and store file_id."""
    if user_id in _fetching_avatars:
        return
    _fetching_avatars.add(user_id)
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if not photos or not photos.photos:
            return
        # Pick the largest size of the first photo
        file_id = photos.photos[0][-1].file_id
        from app.database.session import session_scope as _ss
        from app.repositories.user_repository import UserRepository as _UR
        async with _ss() as sess:
            await _UR(sess).update_photo(user_id, file_id)
            await sess.commit()
    except Exception:
        pass
    finally:
        _fetching_avatars.discard(user_id)


@router.business_message()
async def on_business_message(message: Message, bot: Bot) -> None:
    """Store every incoming/outgoing business message.

    If the *owner* types a message starting with ``!``, treat it as a command,
    dispatch it, and delete the message from the chat (best-effort).
    """
    if not message.business_connection_id:
        logger.warning("Received business_message without a connection id")
        return

    bc_id = message.business_connection_id

    # ── BC connection lookup (cache-first) ────────────────────────────────────
    # owner_telegram_id is needed to classify outgoing vs incoming messages and
    # to gate owner-only features (commands, dot-save, video download).
    # The cache avoids one SELECT per message; it is populated by the
    # business_connection lifecycle handler and invalidated on disconnect.
    cached = _bc_cache.get(bc_id)

    async with session_scope() as session:
        if cached is not None:
            owner_telegram_id, can_reply = cached
            has_connection = True
        else:
            conn_result = await session.execute(
                select(BCModel).where(BCModel.business_connection_id == bc_id)
            )
            conn_row = conn_result.scalar_one_or_none()
            owner_telegram_id = conn_row.user_telegram_id if conn_row else None
            can_reply         = conn_row.can_reply if conn_row else False
            has_connection    = conn_row is not None
            if owner_telegram_id:
                _bc_cache[bc_id] = (owner_telegram_id, can_reply)

        service = MessageService(session)
        # ── Chat filter: skip ingestion if chat is in the exclusion list ────────
        _should_ingest = True
        if owner_telegram_id:
            from app.models.user_settings import UserSettings as _US  # noqa: PLC0415
            _us_f = (await session.execute(
                select(_US).where(_US.owner_telegram_id == owner_telegram_id)
            )).scalar_one_or_none()
            if _us_f:
                _excluded = list(getattr(_us_f, "chat_whitelist", None) or [])
                if message.chat.id in _excluded:
                    _should_ingest = False

        if _should_ingest:
            await service.ingest_new_message(
                message,
                business_connection_id=bc_id,
                owner_telegram_id=owner_telegram_id,
            )

        # ── Background avatar fetch for the sender ────────────────────────────
        if message.from_user and message.from_user.id:
            asyncio.create_task(_try_fetch_avatar(bot, message.from_user.id))

        # ── Streak success notification (fire-and-forget) ─────────────────────
        # Notify the owner via DM when the first incoming message of the day
        # from a contact continues a streak of ≥ 3 days.
        _sender = message.from_user
        _is_incoming = (
            owner_telegram_id is not None
            and _sender is not None
            and _sender.id != owner_telegram_id
        )
        if _is_incoming:
            _chat_id = message.chat.id

            async def _notify_streak(
                _bot=bot,
                _owner=owner_telegram_id,
                _bc_id=bc_id,
                _chat=_chat_id,
            ) -> None:
                try:
                    from app.database.session import get_db_session as _gds
                    from app.models.user import TelegramUser as _TU
                    from app.services.streak_notification_service import (
                        maybe_notify_streak_continued,
                    )
                    async for _sess in _gds():
                        # Resolve contact display name
                        _u = (await _sess.execute(
                            select(_TU).where(_TU.telegram_user_id == _chat)
                        )).scalar_one_or_none()
                        _parts = [p for p in [
                            _u.first_name if _u else None,
                            _u.last_name  if _u else None,
                        ] if p]
                        _name = " ".join(_parts) or f"#{_chat}"
                        # Get all connection_ids for this owner
                        from app.models.business_connection import BusinessConnection as _BCM
                        _cids = [r[0] for r in (await _sess.execute(
                            select(_BCM.business_connection_id).where(
                                _BCM.user_telegram_id == _owner,
                                _BCM.is_blocked.is_(False),
                            )
                        )).fetchall()]
                        await maybe_notify_streak_continued(
                            _bot, _sess, _owner, _cids, _chat, _name
                        )
                except Exception as _exc:
                    logger.warning("Streak success notification task failed: %s", _exc)

            _streak_task = asyncio.create_task(_notify_streak())
            _download_tasks.add(_streak_task)
            _streak_task.add_done_callback(_download_tasks.discard)

        # ── Auto-delete mode (!mute) ──────────────────────────────────────────
        # When the owner runs !mute, every incoming counterpart message is
        # silently deleted for 3 minutes (or until !unmute).
        if _is_incoming and has_connection:
            _del_repo = ChatSettingsRepository(session)
            _delete_active = await _del_repo.is_delete_msgs_active(bc_id, message.chat.id)
            logger.debug(
                "mute check: chat=%s msg_id=%s delete_active=%s",
                message.chat.id, message.message_id, _delete_active,
            )
            if _delete_active:
                async def _do_delete(
                    _bot=bot,
                    _chat_id=message.chat.id,
                    _msg_id=message.message_id,
                    _bc_id=bc_id,
                ) -> None:
                    try:
                        await _bot.delete_message(
                            chat_id=_chat_id,
                            message_id=_msg_id,
                            business_connection_id=_bc_id,
                        )
                        logger.info("mute: deleted msg_id=%s chat=%s", _msg_id, _chat_id)
                    except Exception as _exc:
                        logger.warning("mute: delete FAILED msg_id=%s chat=%s: %s", _msg_id, _chat_id, _exc)

                _del_task = asyncio.create_task(_do_delete())
                _download_tasks.add(_del_task)
                _del_task.add_done_callback(_download_tasks.discard)
                return  # skip all further processing for this message

        # ── Media download / cache (view-once only) ───────────────────────────
        # Regular media file_ids stored in the `messages` table never expire,
        # so there is no need to download their bytes — the file_id is enough
        # to resend the media when a delete notification arrives.
        # Any reply from the owner fires the handler; _handle_dot_save itself
        # decides whether the target is self-destructing (Bot API can't download
        # it) and only forwards in that case, so normal-media replies are
        # silently ignored without notifying the owner.
        sender = message.from_user
        if (
            has_connection
            and sender is not None
            and sender.id == owner_telegram_id
            and message.reply_to_message is not None
        ):
            _save_task = asyncio.create_task(
                _handle_dot_save(
                    bot=bot,
                    owner_id=owner_telegram_id,
                    business_connection_id=bc_id,
                    chat_id=message.chat.id,
                    reply_to_message_id=message.reply_to_message.message_id,
                    dot_message_id=message.message_id,
                )
            )
            _download_tasks.add(_save_task)
            _save_task.add_done_callback(_download_tasks.discard)

        # --- Command detection ---
        if has_connection and sender is not None and message.text and message.text.startswith("!"):
            if sender.id == owner_telegram_id:
                # Full command set for the owner
                parsed = commands.parse_command(message.text)
                if parsed:
                    cmd, args = parsed
                    logger.info(
                        "Owner command !%s from user=%s in chat=%s",
                        cmd, sender.id, message.chat.id,
                    )
                    _replied_text = (
                        message.reply_to_message.text
                        or message.reply_to_message.caption
                        if message.reply_to_message else None
                    )
                    await commands.dispatch(
                        cmd, args,
                        bot=bot,
                        owner_id=owner_telegram_id,
                        chat_id=message.chat.id,
                        business_connection_id=bc_id,
                        message_id=message.message_id,
                        session=session,
                        can_reply=can_reply,
                        replied_text=_replied_text,
                    )

        # --- Video link detection (Reels / TikTok / YouTube Shorts) ---
        text_to_scan = message.text or message.caption or ""
        if text_to_scan and owner_telegram_id:
            video_match = extract_video_url(text_to_scan)
            # If link was sent by contact, check per-scenario setting
            if video_match and _is_incoming:
                from app.models.user_settings import UserSettings as _US          # noqa: PLC0415
                from app.models.business_connection import BusinessConnection as _BC  # noqa: PLC0415
                _us_v = (await session.execute(
                    select(_US).where(_US.owner_telegram_id == owner_telegram_id)
                )).scalar_one_or_none()
                # Is the contact also running the bot? (mutual connection)
                _contact_has_bot = (await session.execute(
                    select(_BC.business_connection_id).where(
                        _BC.user_telegram_id == message.chat.id,
                        _BC.is_blocked.is_(False),
                    ).limit(1)
                )).scalar_one_or_none() is not None
                if _contact_has_bot:
                    # Mutual — their bot will download too; skip by default
                    if not getattr(_us_v, "dl_contact_videos_mutual", False):
                        video_match = None
                else:
                    # Solo — only you have the bot; respect the solo setting
                    if not getattr(_us_v, "dl_contact_videos", True):
                        video_match = None
            if video_match:
                url, platform = video_match
                chat_id = message.chat.id
                # Symmetric key: same regardless of which side's bot fires first.
                # Ensures only one download happens even when both participants
                # have the bot connected to their accounts.
                lo, hi = sorted((owner_telegram_id, chat_id))
                key = (lo, hi, url)
                if key not in _in_flight:
                    logger.info(
                        "Video link detected (%s) in chat=%s can_reply=%s",
                        platform, chat_id, can_reply,
                    )
                    if not can_reply:
                        asyncio.create_task(permissions.notify_missing(
                            bot, owner_telegram_id, "can_reply",
                            "автоскачивание видео",
                        ))
                    else:
                        _in_flight.add(key)

                        # All platforms: auto-download at 1080p (falls back to 720p → best).
                        # Targeting 1080p so Telegram's own compression still yields
                        # acceptable quality on mobile.
                        async def _guarded_download(
                            _bot=bot,
                            _chat_id=chat_id,
                            _conn_id=message.business_connection_id,
                            _url=url,
                            _platform=platform,
                            _key=key,
                            _link_msg_id=message.message_id,
                        ) -> None:
                            async with _download_semaphore:
                                try:
                                    await handle_video_link(
                                        bot=_bot,
                                        chat_id=_chat_id,
                                        business_connection_id=_conn_id,
                                        url=_url,
                                        platform=_platform,
                                        link_message_id=_link_msg_id,
                                        quality="1080",
                                    )
                                finally:
                                    _in_flight.discard(_key)

                        task = asyncio.create_task(_guarded_download())
                        _download_tasks.add(task)
                        task.add_done_callback(_download_tasks.discard)


# ── Edit handler ──────────────────────────────────────────────────────────────

@router.edited_business_message()
async def on_edited_business_message(message: Message, bot: Bot) -> None:
    """Preserve the original version, append the edited version, notify owner."""

    if not message.business_connection_id:
        logger.warning("Received edited_business_message without a connection id")
        return

    # Everything that touches the DB happens in one session.
    outcome = None
    connection = None
    is_muted = False

    async with session_scope() as session:
        service = MessageService(session)
        outcome = await service.ingest_edited_message(
            message, business_connection_id=message.business_connection_id
        )

        if outcome.is_first_capture:
            return

        result = await session.execute(
            select(BCModel).where(
                BCModel.business_connection_id == message.business_connection_id
            )
        )
        connection = result.scalar_one_or_none()

        if connection is None:
            logger.warning(
                "No stored BusinessConnection for id=%s; skipping edit notification",
                message.business_connection_id,
            )
            return

        sender = message.from_user
        if sender is not None and sender.id == connection.user_telegram_id:
            return  # Owner edited their own outgoing message.

        if connection.is_blocked or not connection.notifications_enabled:
            return

        # Check per-chat mute.
        chat_repo = ChatSettingsRepository(session)
        is_muted = await chat_repo.is_muted(
            message.business_connection_id, message.chat.id
        )

    if is_muted:
        logger.info(
            "Chat %s is muted; skipping edit notification", message.chat.id
        )
        return

    # --- Build and send notification ---
    counterpart = _counterpart_label(message.chat, connection.user_telegram_id)
    owner_id = connection.user_telegram_id

    has_media = outcome.previous_file_id is not None
    media_lbl = _media_label(outcome.previous_media_type) if has_media else None

    prev_text_part = _preview(outcome.previous_text or outcome.previous_caption)
    new_text_part  = _preview(message.text or message.caption)

    if has_media and not prev_text_part and not new_text_part:
        notification = (
            f"{E.PENCIL} {counterpart} отредактировал(а) {media_lbl}.\n"
            f"<i>(подпись не изменилась)</i>"
        )
    elif has_media:
        notification = (
            f"{E.PENCIL} {counterpart} отредактировал(а) {media_lbl}:\n\n"
            f"{E.MAGNIFIER} <b>Прошлая подпись:</b>\n«{prev_text_part or '—'}»\n\n"
            f"📝 <b>Новая подпись:</b>\n«{new_text_part or '—'}»"
        )
    else:
        notification = (
            f"{E.PENCIL} {counterpart} отредактировал(а) сообщение:\n\n"
            f"{E.MAGNIFIER} <b>Прошлое значение:</b>\n«{prev_text_part or '—'}»\n\n"
            f"📝 <b>Новое значение:</b>\n«{new_text_part or '—'}»"
        )

    try:
        await bot.send_message(chat_id=owner_id, text=notification, parse_mode="HTML")
        if has_media and outcome.previous_file_id:
            await _try_send_media(
                bot, owner_id,
                outcome.previous_media_type, outcome.previous_file_id,
            )
    except Exception:
        logger.exception(
            "Failed to notify owner user_telegram_id=%s about edit", owner_id
        )


# ── Delete handler ────────────────────────────────────────────────────────────

@router.deleted_business_messages()
async def on_deleted_business_messages(deleted: BusinessMessagesDeleted, bot: Bot) -> None:
    """Mark messages deleted, run panic detection, notify owner.

    See module docstring: Telegram does not resend deleted content, so this
    only works for messages the bot had already captured.

    Panic logic
    -----------
    - Single event with ≥ 3 messages: ONE grouped ⚠️ notification (no individual ones).
    - Single event with < 3 messages: individual notifications as usual.
    - Cross-event: if the rolling 60-second window total crosses the threshold,
      an ADDITIONAL ⚠️ alert fires (individual notifications for that event are
      still sent because they carry the media).
    """
    if not deleted.business_connection_id:
        logger.warning("Received deleted_business_messages without a connection id")
        return

    to_notify: list[DBMessage] = []
    connection: BCModel | None = None

    async with session_scope() as session:
        # Fetch connection once for the whole event.
        result = await session.execute(
            select(BCModel).where(
                BCModel.business_connection_id == deleted.business_connection_id
            )
        )
        connection = result.scalar_one_or_none()

        service = MessageService(session)
        for message_id in deleted.message_ids:
            removed = await service.mark_deleted(
                business_connection_id=deleted.business_connection_id,
                chat_id=deleted.chat.id,
                message_id=message_id,
            )

            if (
                connection is None
                or removed is None
                or removed.sender_telegram_id == connection.user_telegram_id
                or connection.is_blocked
                or not connection.notifications_enabled
            ):
                continue

            to_notify.append(removed)

        # Invalidate AI analysis cache whenever any messages are deleted so the
        # user doesn't see a stale report after clearing their chat history.
        if connection is not None and deleted.message_ids:
            from app.services.ai_analysis_service import invalidate_cache  # noqa: PLC0415
            await invalidate_cache(session, connection.user_telegram_id, deleted.chat.id)
            await session.commit()

        # Check per-chat mute (same chat for the whole event).
        if to_notify:
            chat_repo = ChatSettingsRepository(session)
            if await chat_repo.is_muted(
                deleted.business_connection_id, deleted.chat.id
            ):
                logger.info(
                    "Chat %s is muted; skipping delete notifications", deleted.chat.id
                )
                to_notify.clear()

        # Invalidate any cached AI analysis for this chat — deleting messages
        # means the cached result is now based on incomplete history.
        if connection is not None:
            await ai_analysis_service.invalidate_cache(
                session, connection.user_telegram_id, deleted.chat.id
            )

    if not to_notify or connection is None:
        return

    counterpart = _counterpart_label(deleted.chat, connection.user_telegram_id)
    owner_id = connection.user_telegram_id

    # ── Panic detection ──────────────────────────────────────────────────────
    chat_key = f"{deleted.business_connection_id}:{deleted.chat.id}"
    is_bulk = len(to_notify) >= _PANIC_THRESHOLD

    # Record all deletions; get cross-event panic status.
    # (If it's a bulk event, cross-event alert would be redundant — suppress it.)
    is_cross_event_panic, total_in_window = _panic_tracker.record(
        chat_key, len(to_notify)
    )

    if is_bulk:
        # Large single event: one grouped notification.
        await _send_panic_bulk(bot, owner_id, counterpart, to_notify)
    else:
        # Small event: individual notifications with media.
        for removed in to_notify:
            await _send_single_delete_notification(bot, owner_id, counterpart, removed)

        # Cross-event panic: additional summary alert.
        if is_cross_event_panic:
            await _send_cross_event_panic(bot, owner_id, counterpart, total_in_window)


# ── Telegram Stars payment handlers ──────────────────────────────────────────


# ── Music download callback ───────────────────────────────────────────────────

@router.callback_query(F.data == "mp3_noop")
async def on_mp3_noop(callback: CallbackQuery) -> None:
    """Page-counter button — dismiss spinner, do nothing."""
    await callback.answer()


@router.callback_query(F.data.startswith("mp3n:"))
async def on_mp3_navigate(callback: CallbackQuery, bot: Bot) -> None:
    """Navigate between pages of !mp3 search results."""
    await callback.answer()

    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        return
    _, session_key, page_str = parts
    try:
        page = int(page_str)
    except ValueError:
        return

    session = audio_service.get_session(session_key)
    if session is None:
        try:
            await callback.message.edit_text(  # type: ignore[union-attr]
                "⌛ Сессия истекла — выполните поиск заново (<code>!mp3 название</code>).",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    entries     = session.entries
    total_pages = (len(entries) + audio_service.PAGE_SIZE - 1) // audio_service.PAGE_SIZE
    page        = max(0, min(page, total_pages - 1))

    markup = commands.build_page_markup(entries, session_key, page)

    # Determine bc_id / chat_id from the first entry's cached result
    first_result = audio_service.get(entries[0]["key"]) if entries else None
    bc_id    = first_result.bc_id    if first_result else None
    chat_id  = first_result.chat_id  if first_result else None
    msg_id   = callback.message.message_id  # type: ignore[union-attr]

    header = commands._page_header(session.query, page, total_pages, total=len(entries))

    try:
        if bc_id and chat_id:
            await bot.edit_message_text(
                business_connection_id=bc_id,
                chat_id=chat_id,
                message_id=msg_id,
                text=header,
                parse_mode="HTML",
                reply_markup=markup,
            )
        else:
            await callback.message.edit_text(  # type: ignore[union-attr]
                header, parse_mode="HTML", reply_markup=markup,
            )
    except Exception as exc:
        logger.debug("mp3 navigate: edit failed: %s", exc)


@router.callback_query(F.data.startswith("mp3:"))
async def on_mp3_callback(callback: CallbackQuery, bot: Bot) -> None:
    """User picked a track from the !mp3 search results — download and deliver."""
    await callback.answer("⏳ Загружаю трек…")

    key    = (callback.data or "").split(":", 1)[1]
    result = audio_service.get(key)

    if result is None:
        # Result expired (>10 min) or unknown key
        try:
            await callback.message.edit_text(  # type: ignore[union-attr]
                "⌛ Сессия истекла — выполните поиск заново (<code>!mp3 название</code>).",
                parse_mode="HTML",
            )
        except Exception:
            pass
        return

    bc_id    = result.bc_id
    chat_id  = result.chat_id
    msg_id   = callback.message.message_id  # type: ignore[union-attr]

    async def _edit_text(text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        try:
            await bot.edit_message_text(
                business_connection_id=bc_id,
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup,
            )
        except Exception:
            pass

    # Build a nice "downloading" message with artist + duration
    dur_str    = audio_service.fmt_duration(result.duration)
    artist_str = f"\n👤 {html_escape(result.uploader)}" if result.uploader else ""
    dur_label  = f"  ·  {dur_str}" if dur_str != "?:??" else ""
    await _edit_text(
        f"⏳ <b>Загружаю…</b>\n"
        f"🎵 {html_escape(result.title)}"
        f"{artist_str}{dur_label}"
    )

    tmp_dir = tempfile.mkdtemp(prefix="audbot_")
    try:
        path, title, uploader, duration = await audio_service.download(
            result.url, tmp_dir,
            fallback_title=result.title,
            fallback_uploader=result.uploader,
        )

        await bot.edit_message_media(
            business_connection_id=bc_id,
            chat_id=chat_id,
            message_id=msg_id,
            media=InputMediaAudio(
                media=FSInputFile(path),
                title=title,
                performer=uploader or None,
                duration=duration or None,
            ),
        )
        logger.info("mp3: sent '%s' to chat_id=%s", title, chat_id)

    except Exception as exc:
        logger.warning("mp3: download/send failed for %s: %s", result.url, exc)
        # Restore the search keyboard so the user can pick another track
        session   = audio_service.get_session(result.session_key) if result.session_key else None
        if session:
            entries     = session.entries
            total_pages = (len(entries) + audio_service.PAGE_SIZE - 1) // audio_service.PAGE_SIZE
            markup      = commands.build_page_markup(entries, result.session_key, page=0)
            header      = commands._page_header(session.query, 0, total_pages, total=len(entries))
            await _edit_text(
                f"❌ <b>Не удалось скачать трек.</b>\n\n"
                f"{header}\n\n<i>Попробуйте выбрать другой вариант:</i>",
                markup=markup,
            )
        else:
            await _edit_text(
                "❌ <b>Не удалось скачать трек.</b>\n"
                "Попробуйте другое название: <code>!mp3 название</code>"
            )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.callback_query(F.data == "mp3_close")
async def on_mp3_close(callback: CallbackQuery, bot: Bot) -> None:
    """User dismissed the search results — delete the message."""
    await callback.answer()
    msg = callback.message
    if not msg:
        return
    # Try to delete from business chat first, then fall back to plain delete
    try:
        # Extract bc_id + chat_id from cached result via any entry key in the session
        # The message itself carries no bc context, so we find it from session entries
        text = msg.text or ""
        # Attempt direct delete (works if bot owns the message in a private chat)
        await msg.delete()
    except Exception:
        # If delete fails (business chat), just remove the keyboard
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass


# ── Inline mode: @bot название_песни ─────────────────────────────────────────

@router.inline_query()
async def on_inline_query(query: InlineQuery, bot: Bot) -> None:
    """Handle @bot <query> — search YouTube and show result list above input."""
    q       = (query.query or "").strip()
    user_id = query.from_user.id

    if not q:
        # Show the user's recent inline picks instead of an empty list
        history_keys = audio_service.get_history(user_id)
        if not history_keys:
            await query.answer(
                [],
                is_personal=True,
                cache_time=5,
                switch_pm_text="Введите название песни",
                switch_pm_parameter="inline_help",
            )
            return

        base_url = get_settings().webhook_base_url.rstrip("/")
        history_results: list = []
        for key in history_keys:
            cached = audio_service.get(key)
            if cached is None:
                continue
            fid = audio_service.get_cached_file_id(cached.url)
            if fid:
                history_results.append(InlineQueryResultCachedAudio(
                    id=key,
                    audio_file_id=fid,
                ))
            else:
                audio_service.start_predownload(key, cached.url)
                history_results.append(InlineQueryResultAudio(
                    id=key,
                    audio_url=f"{base_url}/audio/{key}",
                    title=cached.title[:60] if cached.title else "Трек",
                    performer=cached.uploader[:40] if cached.uploader else None,
                    audio_duration=cached.duration or None,
                ))

        await query.answer(
            history_results,
            is_personal=True,
            cache_time=0,   # always fresh — history is per-user
        )
        return

    try:
        results = await audio_service.search(q, n=audio_service.SEARCH_N)
    except Exception as exc:
        logger.warning("inline_query: search failed for %r: %s", q, exc)
        results = []

    base_url = get_settings().webhook_base_url.rstrip("/")
    articles: list = []
    for r in results:
        key      = audio_service.store(r["url"], r["title"], r["uploader"],
                                       r["duration"], "inline", 0)
        dur      = audio_service.fmt_duration(r["duration"])
        uploader = r["uploader"][:40] if r["uploader"] else ""

        cached_fid = audio_service.get_cached_file_id(r["url"])
        if cached_fid:
            # Already uploaded — re-use Telegram's file_id, instant delivery
            articles.append(InlineQueryResultCachedAudio(
                id=key,
                audio_file_id=cached_fid,
            ))
        else:
            # Pre-download in background so /audio/{key} responds fast when
            # Telegram fetches it (avoids WEBPAGE_CURL_FAILED timeout).
            audio_service.start_predownload(key, r["url"])
            articles.append(InlineQueryResultAudio(
                id=key,
                audio_url=f"{base_url}/audio/{key}",
                title=r["title"][:60],
                performer=uploader or None,
                audio_duration=r["duration"] or None,
            ))

    await query.answer(
        articles,            # type: ignore[arg-type]
        is_personal=True,
        cache_time=30,
    )


@router.callback_query(F.data.startswith("note_remind:"))
async def on_note_remind(callback: CallbackQuery, bot: Bot) -> None:
    """Handle reminder advance-time selection from the !note inline keyboard."""
    await callback.answer()
    if not callback.from_user or not callback.data:
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        return
    try:
        note_id       = int(parts[1])
        advance_min   = int(parts[2])
        unix_ts       = int(parts[3])
    except ValueError:
        return

    owner_id = callback.from_user.id

    # Edit the DM to remove the keyboard and confirm the choice
    if advance_min == 0:
        result_text = (
            f"✅ <b>Заметка сохранена</b>\n\n"
            f"📝 {callback.message.html_text.split(chr(10))[0].replace('✅ <b>Заметка принята</b>', '').strip()}\n\n"
            f"<i>Напоминание не установлено.</i>"
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
        except Exception:
            pass
        return

    # Create the reminder row
    import datetime as _dt  # noqa: PLC0415
    event_at = _dt.datetime.fromtimestamp(unix_ts, tz=_dt.timezone.utc) if unix_ts else None

    # Load note text directly from the DB — avoids lstrip-on-charset bugs
    # when trying to parse it back out of the formatted DM message.
    note_text = "(заметка)"
    try:
        from app.database.session import get_db_session as _gds  # noqa: PLC0415
        from app.models.contact_note import ContactNote  # noqa: PLC0415
        async for _s in _gds():
            _note = await _s.get(ContactNote, note_id)
            if _note:
                note_text = _note.text
    except Exception:
        logger.exception("note_remind: failed to load note_id=%s from DB", note_id)

    try:
        from app.database.session import get_db_session  # noqa: PLC0415
        from app.repositories.note_reminder_repository import NoteReminderRepository  # noqa: PLC0415
        async for db_session in get_db_session():
            repo = NoteReminderRepository(db_session)
            # Advance reminder (e.g. 15 min before)
            await repo.create(
                owner_telegram_id=owner_id,
                note_text=note_text,
                event_at=event_at,
                advance_minutes=advance_min,
            )
            # At-event reminder (exactly at event_at)
            if event_at is not None:
                await repo.create(
                    owner_telegram_id=owner_id,
                    note_text=note_text,
                    event_at=event_at,
                    advance_minutes=0,
                )
            await db_session.commit()

        date_str = event_at.strftime("%d.%m.%Y в %H:%M") if event_at else "—"
        label = f"{advance_min} мин" if advance_min < 60 else f"{advance_min // 60} ч"
        try:
            await callback.message.edit_text(  # type: ignore[union-attr]
                f"✅ <b>Заметка сохранена</b>\n\n"
                f"📅 {date_str}\n"
                f"📝 {note_text}\n\n"
                f"⏰ Напомню за <b>{label}</b> до события и в момент начала.",
                parse_mode="HTML",
                reply_markup=None,
            )
        except Exception:
            pass
    except Exception:
        logger.exception("note_remind callback: failed to create reminder for owner %s", owner_id)
        try:
            await bot.send_message(
                chat_id=owner_id,
                text="❌ Не удалось сохранить напоминание. Попробуй ещё раз.",
            )
        except Exception:
            pass


@router.callback_query(F.data == "noop")
async def on_noop(call: CallbackQuery) -> None:
    await call.answer()


# ── Relationship friend-request inline buttons ────────────────────────────────

@router.callback_query(F.data.startswith("rel_accept:") | F.data.startswith("rel_decline:"))
async def on_rel_friend_respond(callback: CallbackQuery, bot: Bot) -> None:
    """Handle Accept / Decline buttons on a friend-request DM."""
    if not callback.from_user or not callback.data:
        await callback.answer()
        return

    accept    = callback.data.startswith("rel_accept:")
    suffix    = "rel_accept:" if accept else "rel_decline:"
    try:
        requester_id = int(callback.data[len(suffix):])
    except ValueError:
        await callback.answer("Неверный запрос.")
        return

    responder_id = callback.from_user.id

    from app.repositories.relationship_repository import RelationshipRepository
    from app.models.user import TelegramUser

    async with session_scope() as session:
        repo = RelationshipRepository(session)
        try:
            rel = await repo.respond(responder_id, requester_id, accept)
            await session.commit()
        except ValueError as exc:
            await callback.answer(str(exc), show_alert=True)
            return

        # Resolve names for notification texts
        resp_row = (await session.execute(
            select(TelegramUser).where(TelegramUser.telegram_user_id == responder_id)
        )).scalar_one_or_none()
        resp_parts = [p for p in [
            resp_row.first_name if resp_row else None,
            resp_row.last_name  if resp_row else None,
        ] if p]
        resp_name = " ".join(resp_parts) or f"#{responder_id}"

    # Determine whether the original message lives in a business chat
    bc_id = getattr(callback.message, "business_connection_id", None) if callback.message else None

    async def _edit(text: str, parse_mode: str | None = None) -> None:
        """Edit the original invitation message, handling both bot-DM and business-chat cases."""
        if not callback.message:
            return
        try:
            if bc_id:
                await bot.edit_message_text(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    business_connection_id=bc_id,
                    text=text,
                    parse_mode=parse_mode,
                )
            else:
                await callback.message.edit_text(text, parse_mode=parse_mode)
        except Exception:
            pass

    if accept:
        sender_name = (
            callback.message.text.split("<b>")[1].split("</b>")[0]
            if callback.message and callback.message.text and "<b>" in callback.message.text
            else "..."
        )
        await _edit(
            f"✅ Вы приняли запрос дружбы от <b>{sender_name}</b>!\n\n"
            f"Откройте мини-приложение, чтобы отправить подарок 🎁",
            parse_mode="HTML",
        )
        await callback.answer("✅ Запрос принят!")
        # Notify the requester via bot DM
        try:
            await bot.send_message(
                requester_id,
                f"💛 <b>{resp_name}</b> принял(а) твой запрос дружбы!\n"
                f"Отправь первый подарок, чтобы начать прокачивать отношения 🎁",
                parse_mode="HTML",
                reply_markup=_miniapp_kb("🎁 Открыть Связи", "/app#interact"),
            )
        except Exception:
            pass
    else:
        await _edit("❌ Запрос на дружбу отклонён.")
        await callback.answer("Запрос отклонён.")
        # Notify the requester via bot DM
        try:
            await bot.send_message(
                requester_id,
                f"😔 <b>{resp_name}</b> отклонил(а) твой запрос дружбы.",
                parse_mode="HTML",
            )
        except Exception:
            pass


@router.chosen_inline_result()
async def on_chosen_inline_result(result: ChosenInlineResult, bot: Bot) -> None:
    """Record the chosen track in the user's history for next empty-query open."""
    key     = result.result_id
    user_id = result.from_user.id
    audio_service.add_to_history(user_id, key)
    logger.info("chosen_inline_result: key=%s user=%s", key, user_id)


@router.pre_checkout_query()
async def on_pre_checkout_query(query: PreCheckoutQuery) -> None:
    """Validate and accept Stars payment pre-checkout queries."""
    ok_prefixes = ("subscription_", "vip_subscription_", "coins_", "pet_revive_")
    if query.invoice_payload.startswith(ok_prefixes):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Unknown product")


def _dur_label(days: int) -> str:
    if days >= 360:   return "12 месяцев"
    if days >= 170:   return "6 месяцев"
    if days >= 80:    return "3 месяца"
    if days >= 29:    return "1 месяц"
    return f"{days} дней"


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot) -> None:
    """Handle successful Stars payments: coins purchase or subscription activation."""
    payment = message.successful_payment
    payload = payment.invoice_payload if payment else ""

    user_id    = message.from_user.id
    charge_id  = payment.telegram_payment_charge_id
    stars_paid = payment.total_amount

    # ── Pet revival ──────────────────────────────────────────────────────────
    if payload.startswith("pet_revive_"):
        # Payload: pet_revive_{user_id}_{pet_id}
        parts = payload.split("_")
        try:
            pet_id = int(parts[-1])
        except (ValueError, IndexError):
            logger.error("Malformed pet_revive payload: %s", payload)
            return

        from app.repositories.pet_repository import PetRepository
        try:
            async with session_scope() as session:
                repo = PetRepository(session)
                pet = await repo.revive_pet(user_id, pet_id)
                await session.commit()
        except ValueError as exc:
            logger.warning("Pet revival failed for user=%s pet=%s: %s", user_id, pet_id, exc)
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text="😔 Не удалось возродить питомца. Возврат Stars будет выполнен автоматически.",
                )
            except Exception:
                pass
            return

        pet_name = pet.get("pet_name", "питомец")
        revival_count = pet.get("revival_count", 1)
        max_revivals  = pet.get("max_revivals", 3)
        revivals_left = max_revivals - revival_count
        logger.info(
            "Pet revived: user=%s pet_id=%s revival=%s/%s stars=%s charge=%s",
            user_id, pet_id, revival_count, max_revivals, stars_paid, charge_id,
        )
        text = (
            f"🌟 <b>Питомец возрождён!</b>\n\n"
            f"«{pet_name}» снова живёт — весь прогресс сохранён!\n\n"
            f"Осталось возрождений: <b>{revivals_left}</b> из <b>{max_revivals}</b>\n\n"
            f"Открой мини-приложение, чтобы продолжить заботиться о питомце."
        )
        try:
            await bot.send_message(
                chat_id=user_id, text=text, parse_mode="HTML",
                reply_markup=_miniapp_kb("🐾 Открыть питомца", "/app?tab=casino"),
            )
        except Exception:
            logger.exception("Failed to send pet revival confirmation to user %s", user_id)
        return

    # ── Coin package purchase ─────────────────────────────────────────────────
    if payload.startswith("coins_"):
        # Payload: coins_{user_id}_{package_id}
        parts = payload.split("_", 2)
        package_id = parts[2] if len(parts) == 3 else None

        from app.repositories.shop_repository import COIN_PACKAGES, ShopRepository
        pkg = COIN_PACKAGES.get(package_id) if package_id else None

        if pkg:
            async with session_scope() as session:
                shop_repo = ShopRepository(session)
                new_balance = await shop_repo.add_coins_from_purchase(user_id, pkg["coins"])
                await session.commit()
            logger.info(
                "Coins purchased: user=%s package=%s coins=%s stars=%s charge=%s balance_after=%s",
                user_id, package_id, pkg["coins"], stars_paid, charge_id, new_balance,
            )
            bonus_line = f"🎁 Бонус {pkg['bonus']} уже учтён!\n\n" if pkg.get("bonus") else ""
            text = (
                f"🪙 <b>Монеты зачислены!</b>\n\n"
                f"{bonus_line}"
                f"<b>+{pkg['coins']:,}".replace(",", " ") + f" монет</b> добавлено на баланс.\n"
                f"Баланс: <b>{new_balance:,}".replace(",", " ") + " 🪙</b>\n\n"
                f"Открой мини-приложение, чтобы потратить!"
            )
        else:
            text = "🪙 Монеты зачислены! Открой мини-приложение."

        try:
            await bot.send_message(
                chat_id=user_id, text=text, parse_mode="HTML",
                reply_markup=_miniapp_kb("🪙 Потратить монеты", "/app#giveaway"),
            )
        except Exception:
            logger.exception("Failed to send coins confirmation to user %s", user_id)
        return

    # ── Subscription activation ───────────────────────────────────────────────
    is_premium = payload.startswith("subscription_")
    is_vip     = payload.startswith("vip_subscription_")
    if not (is_premium or is_vip):
        return

    # Payload formats:
    #   Premium: "subscription_{user_id}_{duration_days}"
    #   VIP:     "vip_subscription_{user_id}_{duration_days}"
    parts = payload.split("_")
    try:
        duration_days = int(parts[-1]) if len(parts) >= 3 else None
    except (ValueError, IndexError):
        duration_days = None

    sub_type = "vip" if is_vip else "premium"

    async with session_scope() as session:
        sub_repo = SubscriptionRepository(session)
        if sub_type == "vip":
            config = await sub_repo.get_vip_config()
        else:
            config = await sub_repo.get_config()
        effective_days = duration_days if duration_days and duration_days >= 1 else config.duration_days
        await sub_repo.activate(user_id, charge_id, stars_paid, effective_days, sub_type=sub_type)
        await session.commit()

    dur_label = _dur_label(effective_days)
    logger.info(
        "%s subscription activated: user=%s stars=%s charge=%s duration=%sd",
        sub_type.upper(), user_id, stars_paid, charge_id, effective_days,
    )

    if sub_type == "vip":
        text = (
            f"💎 <b>VIP подписка активирована!</b>\n\n"
            f"Добро пожаловать в VIP! Доступ открыт на <b>{dur_label}</b>.\n\n"
            f"👑 Твой бейдж, скины питомца и AI-анализ уже активны — открой мини-приложение."
        )
    else:
        text = (
            f"⭐ <b>Premium активирован!</b>\n\n"
            f"Спасибо за поддержку! Подписка действует <b>{dur_label}</b>.\n\n"
            f"🎁 Все бонусы уже активны — открой мини-приложение, чтобы увидеть их."
        )

    try:
        await bot.send_message(
            chat_id=user_id, text=text, parse_mode="HTML",
            reply_markup=_miniapp_kb("⭐ Открыть мини-приложение"),
        )
    except Exception:
        logger.exception("Failed to send subscription confirmation to user %s", user_id)
