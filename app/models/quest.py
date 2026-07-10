"""DailyQuestCompletion — tracks which daily quests a user has claimed today."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DailyQuestCompletion(Base):
    __tablename__ = "daily_quest_completions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    quest_id: Mapped[str] = mapped_column(String(32), nullable=False)
    quest_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    reward: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "owner_telegram_id",
            "quest_id",
            "quest_date",
            name="uq_daily_quest_completion",
        ),
    )
