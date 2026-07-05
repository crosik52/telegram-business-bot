"""Message and message-edit-history models.

Messages are append-only: once stored, the row is never overwritten. Edits
produce a new `MessageEditHistory` row and update denormalized "current"
fields on the `Message` row for fast querying, while the original content
remains untouched in `original_text` / `original_caption`.
"""

from __future__ import annotations

import datetime as dt
import enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

if TYPE_CHECKING:
    from app.models.user import TelegramUser


class MediaType(str, enum.Enum):
    NONE = "none"
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    VOICE = "voice"
    AUDIO = "audio"
    DOCUMENT = "document"
    STICKER = "sticker"
    ANIMATION = "animation"
    VIDEO_NOTE = "video_note"
    CONTACT = "contact"
    LOCATION = "location"
    POLL = "poll"
    OTHER = "other"


class Message(Base):
    """A single Telegram Business message (append-only, never overwritten)."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_chat_sent", "chat_id", "sent_at"),
        Index("ix_messages_search_text", "text"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Telegram identifiers
    business_connection_id: Mapped[str] = mapped_column(String(255), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reply_to_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Sender info (denormalized at time of send, preserved even if the
    # profile changes later)
    sender_id: Mapped[int | None] = mapped_column(
        ForeignKey("telegram_users.id"), nullable=True, index=True
    )
    sender_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sender_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_outgoing: Mapped[bool] = mapped_column(Boolean, default=False)

    # Current (possibly edited) content
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Original content, preserved forever regardless of edits
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Media
    media_type: Mapped[MediaType] = mapped_column(
        Enum(MediaType, native_enum=False, length=32), default=MediaType.NONE
    )
    file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_unique_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Lifecycle
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    last_edited_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    edit_count: Mapped[int] = mapped_column(Integer, default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )

    sender: Mapped[TelegramUser | None] = relationship(
        back_populates="messages", foreign_keys=[sender_id]
    )
    edit_history: Mapped[list[MessageEditHistory]] = relationship(
        back_populates="message",
        order_by="MessageEditHistory.edited_at",
        cascade="all, delete-orphan",
    )

    @property
    def has_media(self) -> bool:
        return self.media_type != MediaType.NONE


class MessageEditHistory(Base):
    """Immutable snapshot of a message's content each time it is edited.

    Rows are only ever inserted, never updated or deleted, so the full
    revision history of a message is always reconstructable.
    """

    __tablename__ = "message_edit_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True, nullable=False
    )
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )

    message: Mapped[Message] = relationship(back_populates="edit_history")
