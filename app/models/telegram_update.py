"""Durable, bounded Telegram inbox; completed events retain no message payload."""

import datetime as dt

from sqlalchemy import JSON, BigInteger, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class TelegramUpdate(Base):
    __tablename__ = "telegram_updates"
    __table_args__ = (
        UniqueConstraint("bot_id", "update_id", name="uq_telegram_updates_bot_update"),
        Index("ix_telegram_updates_state_ready", "state", "ready_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger)
    update_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    ready_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC))
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC))
