"""AiAnalysisDailyCount — per-user daily AI analysis usage counter.

One row per (owner_id, date). Survives deploys so the rate-limit is not
reset when the server restarts.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Date, Index, Integer, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AiAnalysisDailyCount(Base):
    """Running count of AI analyses used by an owner on a given UTC date."""

    __tablename__ = "ai_analysis_daily_counts"
    __table_args__ = (
        Index(
            "ix_ai_analysis_daily_counts_owner_date",
            "owner_id",
            "date",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
