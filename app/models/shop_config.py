"""ShopConfig — singleton row with all shop item prices and settings."""
from __future__ import annotations

from sqlalchemy import Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


DEFAULT_SHOP_CONFIG: dict = {
    "double_xp": {
        "label":   "Двойной XP",
        "cost":    200,
        "hours":   24,
        "enabled": True,
    },
    "theme": {
        "label":   "Тема оформления",
        "cost":    100,
        "enabled": True,
        "options": ["default", "dark_forest", "ocean", "sunset", "lavender",
                    "frost", "ember", "violet_dream"],
        # Per-theme price overrides (if absent, falls back to "cost")
        "theme_prices": {
            "default":      0,
            "frost":        500,
            "ember":        500,
            "violet_dream": 750,
        },
    },
    "frame": {
        "label":   "Рамка профиля",
        "cost":    150,
        "enabled": True,
        "options": ["none", "stars", "flowers", "fire", "neon"],
    },
    "pin_chat": {
        "label":   "Закрепить чат",
        "cost":    75,
        "enabled": True,
    },
    "gift": {
        "label":   "Подарок монет",
        "cost":    30,
        "amount":  50,
        "enabled": True,
    },
}


class ShopConfig(Base):
    """Singleton row — always id=1. Admin edits update this row in-place."""

    __tablename__ = "shop_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    items: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=lambda: dict(DEFAULT_SHOP_CONFIG)
    )
