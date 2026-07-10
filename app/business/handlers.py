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

from aiogram import Bot, F, Router
from aiogram.types import BusinessConnection, BusinessMessagesDeleted, Message, PreCheckoutQuery
from sqlalchemy import select

from app.business import commands
from app.business.panic_tracker import PanicTracker
from app.database.session import session_scope
from app.logging_config import get_logger
from app.models.business_connection import BusinessConnection as BCModel
from app.models.message import MediaType, Message as DBMessage
from app.repositories.chat_settings_repository import ChatSettingsRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.message_service import MessageService
from app.services.video_service import extract_video_url, handle_video_link

# Strong references to background download tasks — prevents GC before completion.
_download_tasks: set[asyncio.Task] = set()

# Global semaphore: at most 3 video downloads run concurrently across all chats.
_download_semaphore = asyncio.Semaphore(3)

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
    file_id: str,
    caption: str | None = None,
) -> bool:
    """Resend a Telegram media file to *chat_id* using its stored file_id.

    Returns True on success, False if Telegram rejects the file_id.
    Callers should fall back gracefully — the text notification has already
    been sent.
    """
    kw: dict = {"chat_id": chat_id}
    if caption:
        kw["caption"] = caption

    # Telegram does not support captions for stickers or video notes.
    kw_no_caption: dict = {"chat_id": chat_id}

    try:
        match media_type:
            case MediaType.PHOTO:
                await bot.send_photo(photo=file_id, **kw)
            case MediaType.VIDEO:
                await bot.send_video(video=file_id, **kw)
            case MediaType.VOICE:
                await bot.send_voice(voice=file_id, **kw)
            case MediaType.VIDEO_NOTE:
                await bot.send_video_note(video_note=file_id, **kw_no_caption)
            case MediaType.AUDIO:
                await bot.send_audio(audio=file_id, **kw)
            case MediaType.DOCUMENT:
                await bot.send_document(document=file_id, **kw)
            case MediaType.STICKER:
                await bot.send_sticker(sticker=file_id, **kw_no_caption)
            case MediaType.ANIMATION:
                await bot.send_animation(animation=file_id, **kw)
            case _:
                # CONTACT, LOCATION, POLL etc. have no file_id; skip silently.
                return False
        return True
    except Exception:
        logger.warning(
            "Failed to resend media type=%s to chat_id=%s (file_id may have expired)",
            media_type.value,
            chat_id,
        )
        return False


# ── Notification builders ─────────────────────────────────────────────────────

async def _send_single_delete_notification(
    bot: Bot,
    owner_id: int,
    counterpart: str,
    removed: DBMessage,
) -> None:
    """Send one delete notification for a single removed message."""
    has_media = removed.file_id is not None
    media_lbl = _media_label(removed.media_type) if has_media else None
    text_part = _preview(removed.text or removed.caption)

    if has_media and text_part:
        notification = f"🗑 {counterpart} удалил(а) {media_lbl}:\n\n«{text_part}»"
    elif has_media:
        notification = f"🗑 {counterpart} удалил(а) {media_lbl}."
    elif text_part:
        notification = f"🗑 {counterpart} удалил(а) сообщение:\n\n«{text_part}»"
    else:
        notification = f"🗑 {counterpart} удалил(а) сообщение."

    try:
        await bot.send_message(chat_id=owner_id, text=notification, parse_mode="HTML")
        if has_media and removed.file_id:
            await _try_send_media(
                bot, owner_id, removed.media_type, removed.file_id,
                caption=text_part or None,
            )
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
        f"⚠️ <b>Паник-удаление!</b>\n\n"
        f"{counterpart} удалил(а) <b>{n} сообщений</b> разом:\n"
    ]
    media_to_resend: list[tuple[MediaType, str, str | None]] = []

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

        if has_media and msg.file_id:
            media_to_resend.append((msg.media_type, msg.file_id, text_part or None))

    try:
        await bot.send_message(
            chat_id=owner_id, text="\n".join(lines), parse_mode="HTML"
        )
        for media_type, file_id, caption in media_to_resend:
            await _try_send_media(bot, owner_id, media_type, file_id, caption=caption)
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
        f"⚠️ <b>Паник-детект:</b> {counterpart} удалил(а) уже "
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

    async with session_scope() as session:
        result = await session.execute(
            select(BCModel).where(BCModel.business_connection_id == connection.id)
        )
        record = result.scalar_one_or_none()

        if record is None:
            record = BCModel(
                business_connection_id=connection.id,
                user_telegram_id=connection.user.id,
                user_first_name=connection.user.first_name,
                user_last_name=connection.user.last_name,
                user_username=connection.user.username,
                can_reply=connection.can_reply,
                is_enabled=connection.is_enabled,
            )
            session.add(record)
        else:
            record.can_reply = connection.can_reply
            record.is_enabled = connection.is_enabled
            record.user_first_name = connection.user.first_name
            record.user_last_name = connection.user.last_name
            record.user_username = connection.user.username

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

    logger.info(
        "Business connection %s enabled=%s can_reply=%s",
        connection.id, connection.is_enabled, connection.can_reply,
    )


# ── New message handler ───────────────────────────────────────────────────────

@router.business_message()
async def on_business_message(message: Message, bot: Bot) -> None:
    """Store every incoming/outgoing business message.

    If the *owner* types a message starting with ``!``, treat it as a command,
    dispatch it, and delete the message from the chat (best-effort).
    """
    if not message.business_connection_id:
        logger.warning("Received business_message without a connection id")
        return

    async with session_scope() as session:
        # Always load the connection so we know the owner's ID for is_outgoing.
        conn_result = await session.execute(
            select(BCModel).where(
                BCModel.business_connection_id == message.business_connection_id
            )
        )
        connection = conn_result.scalar_one_or_none()
        owner_telegram_id = connection.user_telegram_id if connection else None

        service = MessageService(session)
        await service.ingest_new_message(
            message,
            business_connection_id=message.business_connection_id,
            owner_telegram_id=owner_telegram_id,
        )

        # --- Owner command detection ---
        sender = message.from_user
        if connection and sender is not None and message.text and message.text.startswith("!"):
            if sender.id == connection.user_telegram_id:
                parsed = commands.parse_command(message.text)
                if parsed:
                    cmd, args = parsed
                    logger.info(
                        "Owner command !%s from user=%s in chat=%s",
                        cmd, sender.id, message.chat.id,
                    )
                    await commands.dispatch(
                        cmd, args,
                        bot=bot,
                        owner_id=connection.user_telegram_id,
                        chat_id=message.chat.id,
                        business_connection_id=message.business_connection_id,
                        message_id=message.message_id,
                        session=session,
                    )

        # --- Video link detection (Reels / TikTok / YouTube Shorts) ---
        text_to_scan = message.text or message.caption or ""
        if text_to_scan and owner_telegram_id:
            video_match = extract_video_url(text_to_scan)
            if video_match:
                url, platform = video_match
                chat_id = message.chat.id
                # Symmetric key: same regardless of which side's bot fires first.
                # Ensures only one download happens even when both participants
                # have the bot connected to their accounts.
                lo, hi = sorted((owner_telegram_id, chat_id))
                key = (lo, hi, url)
                if key not in _in_flight:
                    _in_flight.add(key)
                    logger.info(
                        "Video link detected (%s) in chat=%s, scheduling download",
                        platform, chat_id,
                    )

                    async def _guarded_download(
                        _bot=bot,
                        _chat_id=chat_id,
                        _conn_id=message.business_connection_id,
                        _url=url,
                        _platform=platform,
                        _key=key,
                    ) -> None:
                        async with _download_semaphore:
                            try:
                                await handle_video_link(
                                    bot=_bot,
                                    chat_id=_chat_id,
                                    business_connection_id=_conn_id,
                                    url=_url,
                                    platform=_platform,
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
            f"✏️ {counterpart} отредактировал(а) {media_lbl}.\n"
            f"<i>(подпись не изменилась)</i>"
        )
    elif has_media:
        notification = (
            f"✏️ {counterpart} отредактировал(а) {media_lbl}:\n\n"
            f"🔍 <b>Прошлая подпись:</b>\n«{prev_text_part or '—'}»\n\n"
            f"📝 <b>Новая подпись:</b>\n«{new_text_part or '—'}»"
        )
    else:
        notification = (
            f"✏️ {counterpart} отредактировал(а) сообщение:\n\n"
            f"🔍 <b>Прошлое значение:</b>\n«{prev_text_part or '—'}»\n\n"
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


@router.pre_checkout_query()
async def on_pre_checkout_query(query: PreCheckoutQuery) -> None:
    """Validate and accept subscription pre-checkout queries."""
    if query.invoice_payload.startswith("subscription_"):
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Unknown product")


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot) -> None:
    """Activate subscription after a successful Stars payment."""
    payment = message.successful_payment
    if payment is None or not payment.invoice_payload.startswith("subscription_"):
        return

    user_id   = message.from_user.id
    charge_id = payment.telegram_payment_charge_id
    stars_paid = payment.total_amount          # Stars amount (integer)

    async with session_scope() as session:
        sub_repo = SubscriptionRepository(session)
        config   = await sub_repo.get_config()
        await sub_repo.activate(user_id, charge_id, stars_paid, config.duration_days)
        await session.commit()

    logger.info(
        "Subscription activated: user=%s stars=%s charge=%s duration=%sd",
        user_id, stars_paid, charge_id, config.duration_days,
    )

    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"⭐ <b>Подписка активирована!</b>\n\n"
                f"Спасибо за поддержку! Подписка действует <b>{config.duration_days} дней</b>.\n\n"
                f"🎁 Все бонусы уже активны — открой мини-приложение, чтобы увидеть их."
            ),
        )
    except Exception:
        logger.exception("Failed to send subscription confirmation to user %s", user_id)
