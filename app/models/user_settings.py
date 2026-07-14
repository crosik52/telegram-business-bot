"""UserSettings — per-user appearance and preference settings."""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    theme: Mapped[str] = mapped_column(String(50), default="default", server_default="default")
    frame: Mapped[str] = mapped_column(String(50), default="none", server_default="none")
    pinned_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
