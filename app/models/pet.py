"""ChatPet — a shared virtual pet tied to a specific chat relationship.

The pet lives as long as the streak with that interlocutor is active.
Hunger decreases over time; feeding costs coins.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ChatPet(Base):
    __tablename__ = "chat_pets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pet_name: Mapped[str] = mapped_column(String(50), nullable=False)
    species: Mapped[str] = mapped_column(String(20), nullable=False)  # cat/dog/rabbit/hamster/fox
    interlocutor_name: Mapped[str] = mapped_column(
        String(100), nullable=False, default=""
    )
    is_alive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    born_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
    last_fed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    died_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    death_cause: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # "starvation" | "streak_broken"
