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

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

# ── Economy constants (consumed by repository + routes) ───────────────────────

TIER_ORDER    = ["friends", "dating", "married"]
TIER_LABELS   = {"friends": "💛 Друзья", "dating": "❤️ Отношения", "married": "💍 Брак"}
XP_PER_LEVEL  = 200    # XP per level
MAX_REL_LEVEL = 10     # levels per tier (was 5 — extended with titles/perks)

REQUEST_COST     = 50   # coins to send a friend request
GIFT_COST        = 50   # coins for a daily gift (legacy default = "rose")
GIFT_TO_PARTNER  = 40   # coins partner receives from a gift
GIFT_XP          = 100  # XP both sides gain from one gift
GIFT_COOLDOWN_H  = 20   # hours between gifts per sender

# ── Gift catalogue ────────────────────────────────────────────────────────────
# Server-authoritative: gift_id comes from the client but cost/xp are ALWAYS
# taken from this table.  min_tier gates premium gifts behind higher tiers.
GIFT_TYPES: dict[str, dict] = {
    "chocolate": {"emoji": "🍫", "label": "Шоколад",   "cost": 30,   "to_partner": 25,  "xp": 60,   "min_tier": "friends"},
    "rose":      {"emoji": "🌹", "label": "Роза",       "cost": 50,   "to_partner": 40,  "xp": 100,  "min_tier": "friends"},
    "teddy":     {"emoji": "🧸", "label": "Мишка",      "cost": 120,  "to_partner": 90,  "xp": 260,  "min_tier": "friends"},
    "bouquet":   {"emoji": "💐", "label": "Букет",      "cost": 300,  "to_partner": 220, "xp": 700,  "min_tier": "dating"},
    "diamond":   {"emoji": "💎", "label": "Бриллиант",  "cost": 800,  "to_partner": 550, "xp": 2000, "min_tier": "married"},
}

# ── Couple streak ─────────────────────────────────────────────────────────────
# A streak day counts when BOTH partners sent a gift on the same UTC date.
STREAK_XP_BONUS_PER_DAY = 0.02   # +2% gift XP per streak day…
STREAK_XP_BONUS_CAP     = 0.50   # …capped at +50%
# Coins credited to BOTH partners when the streak first reaches the milestone
STREAK_MILESTONES: dict[int, int] = {3: 50, 7: 150, 14: 350, 30: 1000, 100: 5000}

# ── Level titles & perks ──────────────────────────────────────────────────────
# Title by level bracket (applies within any tier)
LEVEL_TITLES: dict[str, list[tuple[int, str]]] = {
    # (min_level, title)
    "friends": [(1, "Знакомые"), (3, "Приятели"), (5, "Лучшие друзья"), (8, "Не разлей вода"), (10, "Легендарная дружба")],
    "dating":  [(1, "Влюблённые"), (3, "Пара"), (5, "Родственные души"), (8, "Идеальная пара"), (10, "Легендарная любовь")],
    "married": [(1, "Молодожёны"), (3, "Крепкая семья"), (5, "Союз сердец"), (8, "Золотая пара"), (10, "Вечная любовь")],
}
# Pet-XP perk grows slightly with relationship level (added to REL_XP_BONUS)
LEVEL_PERK_STEP = 0.005  # +0.5% pet XP per level above 1

# ── Anniversaries ─────────────────────────────────────────────────────────────
ANNIVERSARY_MONTH_BONUS = 200    # coins to both on each month-anniversary
ANNIVERSARY_YEAR_BONUS  = 1500   # coins to both on each year-anniversary

# ── Weekly couple quests ──────────────────────────────────────────────────────
# target counters are tracked per ISO week in Relationship.meta
COUPLE_QUESTS: dict[str, dict] = {
    "gifts3":   {"title": "Обмен подарками", "desc": "Отправьте друг другу 3 подарка за неделю", "target": 3,  "counter": "gifts_week",  "reward_coins": 120, "reward_xp": 150},
    "mutual2":  {"title": "Вместе каждый день", "desc": "2 дня, когда дарили оба",                "target": 2,  "counter": "mutual_week", "reward_coins": 200, "reward_xp": 250},
    "spend500": {"title": "Щедрая неделя",    "desc": "Потратьте вместе 500 монет на подарки",    "target": 500, "counter": "spent_week",  "reward_coins": 300, "reward_xp": 400},
}

# Coin cost to upgrade FROM this tier to the next
UPGRADE_COSTS: dict[str, int] = {
    "friends": 300,
    "dating":  1_000,
}
# Minimum level within the tier required before upgrading
UPGRADE_MIN_LEVEL: dict[str, int] = {
    "friends": 3,   # kept from the 5-level era — friendship stays accessible
    "dating":  7,   # raised (was 5) with the 10-level cap so marriage isn't a midpoint
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

    # "friendship" — stays at friends tier, no romantic progression
    # "romantic"   — can upgrade friends → dating → married (default)
    category: Mapped[str] = mapped_column(
        String(20), nullable=False, default="romantic"
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

    # JSON blob for streak / quests / anniversaries / gift counters.
    # Kept schemaless so new mechanics don't need migrations:
    # {"streak": {"days": int, "best": int, "last_mutual": "YYYY-MM-DD",
    #             "milestones": [3,7]},
    #  "gift_date_a": "YYYY-MM-DD", "gift_date_b": "YYYY-MM-DD",
    #  "week": {"key": "2026-W31", "gifts": int, "mutual": int, "spent": int,
    #           "claimed": ["gifts3"]},
    #  "anniv": {"last": "2026-07"},
    #  "totals": {"gifts": int, "spent": int}}
    meta: Mapped[str | None] = mapped_column(Text, nullable=True)
