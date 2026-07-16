"""Subscription models.

SubscriptionConfig — singleton row with all plan settings (price, benefits, etc.)
UserSubscription   — one row per activated subscription period per user.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

# ── Default benefit values ────────────────────────────────────────────────────
DEFAULT_BENEFITS: dict = {
    "daily_multiplier":  2.0,   # multiply base daily-claim coins
    "daily_bonus_coins": 50,    # flat bonus on top
    "pet_feed_free":     False, # feeds cost 0 coins
    "xp_multiplier":     1.5,   # XP multiplier for all pet actions
    "max_pets_bonus":    2,     # extra alive-pets slots
}


class SubscriptionConfig(Base):
    """Singleton row — always id=1.  Admin edits update this row in-place."""

    __tablename__ = "subscription_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    price_stars: Mapped[int] = mapped_column(Integer, nullable=False, default=99)
    duration_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    title: Mapped[str] = mapped_column(
        String(100), nullable=False, default="Premium подписка"
    )
    description: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="Бонусы и привилегии для подписчиков",
    )
    # JSON dict of benefit settings (keys mirror DEFAULT_BENEFITS)
    benefits: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: dict(DEFAULT_BENEFITS))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )


class UserSubscription(Base):
    """One row per subscription period per user.

    ``status`` is the authoritative lifecycle field:
        'active'    — subscription is in effect and extendable.
        'paused'    — temporarily paused (not yet implemented, reserved).
        'cancelled' — deactivated by admin revoke or replaced by a newer row.
        'refunded'  — payment was refunded.

    ``is_active`` is kept in sync with ``status`` for backwards compatibility:
        status='active'    → is_active=True
        all other statuses → is_active=False
    """

    __tablename__ = "user_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Lifecycle status — kept in sync with is_active (see docstring above).
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    granted_by_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payment_charge_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stars_paid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
