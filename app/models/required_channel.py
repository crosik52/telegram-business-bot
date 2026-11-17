"""Channels the user must subscribe to before using the bot."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class RequiredChannel(Base):
    __tablename__ = "required_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    # @username (with or without @) or "-100…" numeric string
    channel_username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    channel_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    require_premium_boost: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False,
    )
    channel_join_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
    )

    # Convenience: display name for UI
    @property
    def display_title(self) -> str:
        return self.channel_title or self.channel_username

    # Canonical @mention form (strip leading @, re-add for links)
    @property
    def at_username(self) -> str:
        u = self.channel_username.lstrip("@")
        return f"@{u}" if not u.startswith("-") else u

    @property
    def join_url(self) -> str:
        if self.channel_join_url:
            return self.channel_join_url
        u = self.channel_username.lstrip("@")
        return f"https://t.me/{u}" if not u.startswith("-") else ""

    @property
    def boost_url(self) -> str:
        u = self.channel_username.lstrip("@")
        if u.startswith("-100"):
            return f"https://t.me/boost?c={u[4:]}"
        return f"https://t.me/boost/{u}"
