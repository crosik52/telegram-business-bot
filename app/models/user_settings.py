"""UserSettings — per-user appearance and preference settings."""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    theme: Mapped[str] = mapped_column(String(50), default="default", server_default="default")
    frame: Mapped[str] = mapped_column(String(50), default="none", server_default="none")
    pinned_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # JSON list of theme slugs the user has purchased (NULL treated as ["default"])
    owned_themes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # JSON list of chat_ids for which streak notifications are silenced
    muted_streaks: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ── User-configurable preferences ────────────────────────────────────────
    # Global switch: send streak success/reminder notifications at all
    streak_reminders_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    # Download videos from links sent BY a contact (not the owner)
    dl_contact_videos: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    # 'all' = collect every chat; 'whitelist' = only chats in chat_whitelist
    chat_filter_mode: Mapped[str] = mapped_column(
        String(20), default="all", server_default="all", nullable=False
    )
    # JSON list of chat_ids to collect when chat_filter_mode == 'whitelist'
    chat_whitelist: Mapped[list | None] = mapped_column(JSON, nullable=True)
