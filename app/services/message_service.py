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
    previous_file_id: str | None
    previous_media_type: MediaType
    is_first_capture: bool
    is_duplicate: bool = False


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


def _edit_timestamp(message: AiogramMessage) -> dt.datetime:
    value = message.edit_date or message.date
    if isinstance(value, int):
        return dt.datetime.fromtimestamp(value, tz=dt.UTC)
    return value or dt.datetime.now(dt.UTC)


class MessageService:
    """Handles ingestion of new/edited/deleted business messages."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._messages = MessageRepository(session)
        self._users = UserRepository(session)

    async def ingest_new_message(
        self,
        message: AiogramMessage,
        *,
        business_connection_id: str,
        owner_telegram_id: int | None = None,
    ) -> Message:
        existing = await self._messages.get_by_telegram_ids(
            business_connection_id, message.chat.id, message.message_id
        )
        if existing is not None and not existing.is_placeholder:
            if existing.is_edited and existing.original_media_type is None:
                media_type, file_id, unique_id = _resolve_media(message)
                existing.original_text = message.text
                existing.original_caption = message.caption
                existing.original_file_id = file_id
                existing.original_file_unique_id = unique_id
                existing.original_media_type = media_type.value
            return existing
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
            is_outgoing=bool(
                sender
                and owner_telegram_id is not None
                and sender.id == owner_telegram_id
            ),
            text=message.text,
            caption=message.caption,
            original_text=message.text,
            original_caption=message.caption,
            original_file_id=file_id,
            original_file_unique_id=file_unique_id,
            original_media_type=media_type.value,
            media_type=media_type,
            file_id=file_id,
            file_unique_id=file_unique_id,
            sent_at=sent_at,
        )
        if existing is not None:
            # Rehydrate a deletion tombstone without clearing its deletion flags.
            preserved = {"id", "is_deleted", "deleted_at", "is_edited", "last_edited_at", "edit_count"}
            for key, value in record.__dict__.items():
                if not key.startswith("_") and key not in preserved:
                    setattr(existing, key, value)
            existing.is_placeholder = False
            await self._session.flush()
            return existing
        created = await self._messages.create(record)
        logger.info(
            "Stored new business message chat_id=%s message_id=%s media=%s",
            record.chat_id,
            record.message_id,
            media_type.value,
        )
        return created

    async def ingest_edited_message(
        self, message: AiogramMessage, *, business_connection_id: str, source_update_id: int | None = None
    ) -> EditOutcome:
        existing = await self._messages.get_by_telegram_ids(
            business_connection_id, message.chat.id, message.message_id
        )
        if existing is None or existing.is_placeholder:
            # We never saw the original — store it as a new message so no
            # data is lost, then immediately note it arrived pre-edited.
            existing = await self.ingest_new_message(
                message, business_connection_id=business_connection_id
            )
            # First captured edit does not reveal the original text or media.
            existing.original_text = existing.original_caption = None
            existing.original_file_id = existing.original_file_unique_id = None
            existing.original_media_type = None
            existing.is_edited = True
            existing.last_edit_update_id = source_update_id
            existing.last_edited_at = _edit_timestamp(message)
            await self._messages.record_edit(
                existing, new_text=message.text, new_caption=message.caption,
                edited_at=_edit_timestamp(message), snapshot_file_id=existing.file_id,
                snapshot_media_type=existing.media_type.value,
            )
            return EditOutcome(
                message=existing,
                previous_text=None,
                previous_caption=None,
                previous_file_id=None,
                previous_media_type=MediaType.NONE,
                is_first_capture=True,
            )

        previous_text = existing.text
        previous_caption = existing.caption
        previous_file_id = existing.file_id
        previous_media_type = existing.media_type

        edited_at = _edit_timestamp(message)
        media_type, file_id, file_unique_id = _resolve_media(message)
        last_edited = existing.last_edited_at
        if last_edited is not None and last_edited.tzinfo is None:
            last_edited = last_edited.replace(tzinfo=dt.UTC)
        unchanged = (existing.text == message.text and existing.caption == message.caption
                     and existing.file_id == file_id and existing.media_type == media_type)
        if unchanged:
            newer_watermark = (
                last_edited is None or edited_at > last_edited
                or (edited_at == last_edited and source_update_id is not None
                    and (existing.last_edit_update_id is None
                         or source_update_id > existing.last_edit_update_id))
            )
            if newer_watermark:
                existing.last_edited_at = edited_at
                existing.last_edit_update_id = source_update_id
                existing.is_edited = True
            return EditOutcome(existing, previous_text, previous_caption, previous_file_id,
                               previous_media_type, False, is_duplicate=True)
        ambiguous_or_older_same_second = (
            last_edited is not None and edited_at == last_edited
            and (source_update_id is None or existing.last_edit_update_id is None
                 or source_update_id <= existing.last_edit_update_id)
        )
        if ambiguous_or_older_same_second or (last_edited is not None and edited_at < last_edited):
            return EditOutcome(existing, previous_text, previous_caption, previous_file_id,
                               previous_media_type, False, is_duplicate=True)
        await self._messages.record_edit(
            existing,
            new_text=message.text,
            new_caption=message.caption,
            edited_at=edited_at,
            snapshot_file_id=file_id,
            snapshot_media_type=media_type.value,
        )
        existing.last_edit_update_id = source_update_id
        existing.file_id = file_id
        existing.file_unique_id = file_unique_id
        existing.media_type = media_type
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
            previous_file_id=previous_file_id,
            previous_media_type=previous_media_type,
            is_first_capture=False,
        )

    async def mark_deleted(
        self, *, business_connection_id: str, chat_id: int, message_id: int
    ) -> Message | None:
        existing = await self._messages.get_by_telegram_ids(
            business_connection_id, chat_id, message_id
        )
        if existing is None:
            now = dt.datetime.now(dt.UTC)
            await self._messages.create(Message(
                business_connection_id=business_connection_id, chat_id=chat_id,
                message_id=message_id, sent_at=now, is_deleted=True, deleted_at=now,
                is_placeholder=True, media_type=MediaType.OTHER,
            ))
            # Content was never supplied by Telegram; preserve the deletion only.
            return None

        if existing.is_deleted:
            return None
        await self._messages.mark_deleted(
            existing, deleted_at=dt.datetime.now(dt.UTC)
        )
        logger.info(
            "Marked message deleted chat_id=%s message_id=%s", chat_id, message_id
        )
        return existing
