"""Per-chat notification settings (mute, etc.)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ChatSettings(Base):
    """Owner-controlled settings for a single business chat.

    One row per (business_connection_id, chat_id) pair, created lazily
    when the owner first issues a command for that chat.
    """

    __tablename__ = "chat_settings"
    __table_args__ = (
        UniqueConstraint("business_connection_id", "chat_id", name="uq_chat_settings"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_connection_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # When set and in the future, suppress edit/delete notifications for
    # this chat.  NULL means "not muted".
    muted_until: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )
