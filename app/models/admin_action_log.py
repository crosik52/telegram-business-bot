"""Audit log of actions taken by the super-admin from the admin mini app."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AdminActionLog(Base):
    """One row per admin action (settings change, broadcast, etc.)."""

    __tablename__ = "admin_action_log"
    __table_args__ = (Index("ix_admin_action_log_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_owner_telegram_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )
