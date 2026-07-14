"""Referral system models.

ReferralConfig     — singleton row with all referral settings.
Referral           — one row per referrer→referred relationship.
ReferralRewardLog  — audit log of every reward granted.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, JSON, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


# ── Default config ────────────────────────────────────────────────────────────

DEFAULT_MILESTONES = [
    {"count": 1,   "type": "premium_days", "value": 3,    "label": "+3 дня Premium"},
    {"count": 3,   "type": "premium_days", "value": 7,    "label": "+7 дней Premium"},
    {"count": 10,  "type": "premium_days", "value": 30,   "label": "+1 месяц Premium"},
    {"count": 25,  "type": "badge",        "value": "gold_referrer",   "label": "Значок «Золотой рефовод»"},
    {"count": 50,  "type": "badge",        "value": "unique_profile",  "label": "Уникальный профиль"},
    {"count": 100, "type": "premium_days", "value": 365,  "label": "Год Premium бесплатно"},
]

DEFAULT_LEVELS = [
    {"name": "Bronze",   "min": 0,   "max": 4,    "emoji": "🥉", "color": "#CD7F32"},
    {"name": "Silver",   "min": 5,   "max": 9,    "emoji": "🥈", "color": "#C0C0C0"},
    {"name": "Gold",     "min": 10,  "max": 24,   "emoji": "🥇", "color": "#FFD700"},
    {"name": "Platinum", "min": 25,  "max": 49,   "emoji": "💠", "color": "#E5E4E2"},
    {"name": "Diamond",  "min": 50,  "max": None, "emoji": "💎", "color": "#B9F2FF"},
]


class ReferralConfig(Base):
    """Singleton row — always id=1. All referral settings live here."""

    __tablename__ = "referral_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Two-sided reward
    referrer_reward_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    referee_reward_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # Anti-fraud
    min_account_age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_referrals_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    # Milestone rewards (list of dicts)
    milestones: Mapped[list] = mapped_column(
        JSON, nullable=False, default=lambda: list(DEFAULT_MILESTONES)
    )

    # Level definitions
    levels: Mapped[list] = mapped_column(
        JSON, nullable=False, default=lambda: list(DEFAULT_LEVELS)
    )

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )


class Referral(Base):
    """One row per referred user (referred_telegram_id is unique — one referrer per user)."""

    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referrer_telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    referred_telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True, index=True
    )

    # Human-readable info captured at referral creation time
    referred_first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    referred_username: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # status: "pending" | "active" | "fraud"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    fraud_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Whether milestone reward has been evaluated for the *referrer* after this activation
    milestone_checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
    activated_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReferralRewardLog(Base):
    """Immutable log of every reward granted (two-sided or milestone)."""

    __tablename__ = "referral_reward_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    referral_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("referrals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    # "welcome" | "per_activation" | "milestone"
    reward_type: Mapped[str] = mapped_column(String(30), nullable=False)
    reward_value: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "7" days or badge slug
    label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    granted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
