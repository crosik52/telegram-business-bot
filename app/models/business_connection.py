"""Business connection model.

Represents a Telegram Business connection between the bot and the personal
account that enabled it (Telegram Business -> Chatbots settings).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class BusinessConnection(Base):
    """Tracks the lifecycle of a Telegram Business connection."""

    __tablename__ = "business_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_connection_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    can_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    connected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )
