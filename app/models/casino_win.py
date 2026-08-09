"""CasinoWin model — one row per positive-profit casino game outcome.

Used to power the daily / weekly biggest-wins leaderboard.
Rows older than 8 days are never queried; a cleanup job or manual
VACUUM can prune them once persistence is confirmed working.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class CasinoWin(Base):
    __tablename__ = "casino_wins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Telegram user id of the winner
    uid: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    # Display name (truncated Telegram first_name, max 30 chars)
    name: Mapped[str] = mapped_column(String(30), nullable=False)

    # "slots" | "flip" | "mines" | "crash"
    game: Mapped[str] = mapped_column(String(10), nullable=False)

    bet:    Mapped[int] = mapped_column(Integer, nullable=False)
    payout: Mapped[int] = mapped_column(Integer, nullable=False)
    net:    Mapped[int] = mapped_column(Integer, nullable=False)  # payout - bet
    mult:   Mapped[float] = mapped_column(Float, nullable=False)

    ts: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    __table_args__ = (
        # Efficient period-filtered leaderboard queries
        Index("ix_casino_wins_ts_net", "ts", "net"),
    )
