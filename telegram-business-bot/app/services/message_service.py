"""Business logic for ingesting Telegram Business messages.

Every method here is written so that no historical data is ever destroyed:
new messages are inserted, edits append edit-history rows, and deletions
only flip a flag + timestamp on the existing row.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from aiogram.types import Message as AiogramMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models.message import MediaType, Message
from app.repositories.message_repository import MessageRepository
from app.repositories.user_repository import UserRepository

logger = get_logger(__name__)


@dataclass
class EditOutcome:
    """Result of ingesting an edit, including the pre-edit content for notifications."""

    message: Message
    previous_text: str | None
    previous_caption: str | None
    is_first_capture: bool


def _resolve_media(message: AiogramMessage) -> tuple[MediaType, str | None, str | None]:
    """Determine media type and file identifiers from an aiogram message."""

    if message.photo:
        largest = message.photo[-1]
        return MediaType.PHOTO, largest.file_id, largest.file_unique_id
    if message.video:
        return MediaType.VIDEO, message.video.file_id, message.video.file_unique_id
    if message.voice:
        return MediaType.VOICE, message.voice.file_id, message.voice.file_unique_id
    if message.audio:
        return MediaType.AUDIO, message.audio.file_id, message.audio.file_unique_id
    if message.document:
        return (
            MediaType.DOCUMENT,
            message.document.file_id,
            message.document.file_unique_id,
        )
    if message.sticker:
        return (
            MediaType.STICKER,
            message.sticker.file_id,
            message.sticker.file_unique_id,
        )
    if message.animation:
        return (
            MediaType.ANIMATION,
            message.animation.file_id,
            message.animation.file_unique_id,
        )
    if message.video_note:
        return (
            MediaType.VIDEO_NOTE,
            message.video_note.file_id,
            message.video_note.file_unique_id,
        )
    if message.contact:
        return MediaType.CONTACT, None, None
    if message.location:
        return MediaType.LOCATION, None, None
    if message.poll:
        return MediaType.POLL, None, None
    if message.text is not None:
        return MediaType.TEXT, None, None
    return MediaType.OTHER, None, None


class MessageService:
    """Handles ingestion of new/edited/deleted business messages."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._messages = MessageRepository(session)
        self._users = UserRepository(session)

    async def ingest_new_message(
        self, message: AiogramMessage, *, business_connection_id: str
    ) -> Message:
        sender = message.from_user
        media_type, file_id, file_unique_id = _resolve_media(message)
        sent_at = message.date or dt.datetime.now(dt.UTC)

        db_user = None
        if sender is not None and not sender.is_bot:
            db_user = await self._users.upsert(
                telegram_user_id=sender.id,
                username=sender.username,
                first_name=sender.first_name,
                last_name=sender.last_name,
                is_bot=sender.is_bot,
            )

        record = Message(
            business_connection_id=business_connection_id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reply_to_message_id=(
                message.reply_to_message.message_id
                if message.reply_to_message
                else None
            ),
            sender_id=db_user.id if db_user else None,
            sender_telegram_id=sender.id if sender else None,
            sender_username=sender.username if sender else None,
            sender_first_name=sender.first_name if sender else None,
            sender_last_name=sender.last_name if sender else None,
            is_outgoing=bool(sender and getattr(sender, "is_bot", False) is False and False),
            text=message.text,
            caption=message.caption,
            original_text=message.text,
            original_caption=message.caption,
            media_type=media_type,
            file_id=file_id,
            file_unique_id=file_unique_id,
            sent_at=sent_at,
        )
        created = await self._messages.create(record)
        logger.info(
            "Stored new business message chat_id=%s message_id=%s media=%s",
            record.chat_id,
            record.message_id,
            media_type.value,
        )
        return created

    async def ingest_edited_message(
        self, message: AiogramMessage, *, business_connection_id: str
    ) -> EditOutcome:
        existing = await self._messages.get_by_telegram_ids(
            business_connection_id, message.chat.id, message.message_id
        )
        if existing is None:
            # We never saw the original — store it as a new message so no
            # data is lost, then immediately note it arrived pre-edited.
            existing = await self.ingest_new_message(
                message, business_connection_id=business_connection_id
            )
            return EditOutcome(
                message=existing,
                previous_text=None,
                previous_caption=None,
                is_first_capture=True,
            )

        previous_text = existing.text
        previous_caption = existing.caption

        edited_at = (
            dt.datetime.fromtimestamp(message.edit_date, tz=dt.UTC)
            if message.edit_date is not None
            else dt.datetime.now(dt.UTC)
        )
        await self._messages.record_edit(
            existing,
            new_text=message.text,
            new_caption=message.caption,
            edited_at=edited_at,
        )
        logger.info(
            "Recorded edit for chat_id=%s message_id=%s (edit #%s)",
            existing.chat_id,
            existing.message_id,
            existing.edit_count,
        )
        return EditOutcome(
            message=existing,
            previous_text=previous_text,
            previous_caption=previous_caption,
            is_first_capture=False,
        )

    async def mark_deleted(
        self, *, business_connection_id: str, chat_id: int, message_id: int
    ) -> Message | None:
        existing = await self._messages.get_by_telegram_ids(
            business_connection_id, chat_id, message_id
        )
        if existing is None:
            logger.warning(
                "Received delete event for untracked message chat_id=%s message_id=%s",
                chat_id,
                message_id,
            )
            return None

        await self._messages.mark_deleted(
            existing, deleted_at=dt.datetime.now(dt.UTC)
        )
        logger.info(
            "Marked message deleted chat_id=%s message_id=%s", chat_id, message_id
        )
        return existing
