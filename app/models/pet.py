"""ChatPet — a shared virtual pet tied to a specific chat relationship."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ChatPet(Base):
    __tablename__ = "chat_pets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pet_name: Mapped[str] = mapped_column(String(50), nullable=False)
    species: Mapped[str] = mapped_column(String(20), nullable=False)
    interlocutor_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    is_alive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    born_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
    last_fed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    died_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    death_cause: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── v2 fields ────────────────────────────────────────────────────────────
    mood: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    xp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    personality: Mapped[str] = mapped_column(String(20), nullable=False, default="playful")
    last_played_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_cuddled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_feedings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_plays: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cuddles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feed_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── v3 fields ────────────────────────────────────────────────────────────
    # JSON-encoded dict of skill upgrade levels, e.g. {"xp_boost":1,"lucky_paw":0,...}
    upgrades: Mapped[str | None] = mapped_column(String(400), nullable=True)
