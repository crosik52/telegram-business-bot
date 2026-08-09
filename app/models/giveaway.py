"""GiveawayConfig — singleton row that drives the referral giveaway feature."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class GiveawayConfig(Base):
    __tablename__ = "giveaway_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Visibility
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_visible_to_all: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Timing
    deadline: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opens_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Prizes (top-3): display name + image URL (for NFT preview)
    prize_1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prize_2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prize_3: Mapped[str | None] = mapped_column(String(200), nullable=True)
    prize_1_image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    prize_2_image: Mapped[str | None] = mapped_column(String(512), nullable=True)
    prize_3_image: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Optional description shown on the banner
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
    updated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
