"""Relationship model — bonds between mutually-connected bot users.

Three tiers:  💛 Друзья → ❤️ Отношения → 💍 Брак
Each tier has 5 levels.  XP is gained via daily gifts.

Pair normalization
------------------
user_a_id < user_b_id is always enforced at the application layer so that
UniqueConstraint("user_a_id", "user_b_id") prevents duplicate rows regardless
of which side initiates.  ``initiator_id`` records who actually sent the
current-tier request or the most recent upgrade.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

# ── Economy constants (consumed by repository + routes) ───────────────────────

TIER_ORDER    = ["friends", "dating", "married"]
TIER_LABELS   = {"friends": "💛 Друзья", "dating": "❤️ Отношения", "married": "💍 Брак"}
XP_PER_LEVEL  = 200    # XP per level (5 levels per tier)
MAX_REL_LEVEL = 5

REQUEST_COST     = 50   # coins to send a friend request
GIFT_COST        = 50   # coins for a daily gift
GIFT_TO_PARTNER  = 40   # coins partner receives from a gift
GIFT_XP          = 100  # XP both sides gain from one gift
GIFT_COOLDOWN_H  = 20   # hours between gifts per sender

# Coin cost to upgrade FROM this tier to the next
UPGRADE_COSTS: dict[str, int] = {
    "friends": 300,
    "dating":  1_000,
}
# Minimum level within the tier required before upgrading
UPGRADE_MIN_LEVEL: dict[str, int] = {
    "friends": 3,
    "dating":  5,
}

MARRIAGE_DAILY_BONUS = 100  # extra coins in daily claim per active marriage

# Pet XP bonus multipliers per relationship tier (applied to feed/play/cuddle)
REL_XP_BONUS: dict[str, float] = {
    "friends": 1.05,
    "dating":  1.10,
    "married": 1.15,
}


class Relationship(Base):
    """One row per unique user pair.  user_a_id < user_b_id always."""

    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("user_a_id", "user_b_id", name="uq_relationship_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Normalised pair — user_a_id is always the smaller telegram_id
    user_a_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_b_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    # Who sent the latest request / tier upgrade
    initiator_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # "friends" | "dating" | "married"
    rel_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="friends", index=True
    )

    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    xp:    Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # "pending" | "active" | "broken"
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )
    accepted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Last gift timestamp per side (a ↔ user_a_id, b ↔ user_b_id)
    last_gift_a: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_gift_b: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
