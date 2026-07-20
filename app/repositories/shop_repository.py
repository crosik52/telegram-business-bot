"""ShopRepository — coin-spending actions beyond pets and casino."""

from __future__ import annotations

import copy
import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.boost import UserBoost
from app.models.shop_config import DEFAULT_SHOP_CONFIG, ShopConfig
from app.models.user_settings import UserSettings
from app.models.wallet import UserWallet

# ── Coin packages purchasable with Telegram Stars ─────────────────────────────
COIN_PACKAGES: dict[str, dict] = {
    "starter": {"stars": 50,  "coins": 500,  "label": "Стартовый",  "bonus": None},
    "basic":   {"stars": 100, "coins": 1100, "label": "Базовый",    "bonus": "+10%"},
    "popular": {"stars": 250, "coins": 3000, "label": "Популярный", "bonus": "+20%"},
    "max":     {"stars": 500, "coins": 6500, "label": "Максимум",   "bonus": "+30%"},
}

# ── Module-level fallback prices (used if DB has no config row) ───────────────
BOOST_DOUBLE_XP_COST  = 200
BOOST_DOUBLE_XP_HOURS = 24
PIN_CHAT_COST  = 75
THEME_COST     = 100
FRAME_COST     = 150
GIFT_COST      = 30
GIFT_AMOUNT    = 50

VALID_THEMES = {
    "default", "dark_forest", "ocean", "sunset", "lavender",
    "frost", "ember", "violet_dream",
}
VALID_FRAMES = {"none", "stars", "flowers", "fire", "neon"}

# Themes that are free for everyone (always in owned list)
FREE_THEMES = {"default"}


class ShopRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Shop config (singleton) ───────────────────────────────────────────────

    async def _get_shop_cfg(self) -> dict:
        """Load shop config from DB. Falls back to DEFAULT_SHOP_CONFIG."""
        result = await self._session.execute(select(ShopConfig).limit(1))
        cfg = result.scalar_one_or_none()
        if cfg is None:
            return copy.deepcopy(DEFAULT_SHOP_CONFIG)
        return cfg.items or copy.deepcopy(DEFAULT_SHOP_CONFIG)

    async def get_shop_config_admin(self) -> dict:
        """Admin: return full shop config dict."""
        result = await self._session.execute(select(ShopConfig).limit(1))
        cfg = result.scalar_one_or_none()
        if cfg is None:
            # Create the singleton row with defaults
            cfg = ShopConfig(id=1, items=copy.deepcopy(DEFAULT_SHOP_CONFIG))
            self._session.add(cfg)
            await self._session.flush()
        return cfg.items or copy.deepcopy(DEFAULT_SHOP_CONFIG)

    async def update_shop_config(self, items: dict) -> dict:
        """Admin: overwrite shop config. Returns new config."""
        result = await self._session.execute(select(ShopConfig).limit(1))
        cfg = result.scalar_one_or_none()
        if cfg is None:
            cfg = ShopConfig(id=1, items=items)
            self._session.add(cfg)
        else:
            cfg.items = items
        await self._session.flush()
        return cfg.items

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _deduct(self, owner_id: int, cost: int) -> int:
        """Deduct *cost* coins from wallet. Returns new balance.
        Raises ValueError('insufficient_coins') if balance is too low."""
        result = await self._session.execute(
            select(UserWallet)
            .where(UserWallet.owner_telegram_id == owner_id)
            .with_for_update()
        )
        wallet = result.scalar_one_or_none()
        if wallet is None or wallet.balance < cost:
            raise ValueError("insufficient_coins")
        wallet.balance -= cost
        wallet.total_spent = (wallet.total_spent or 0) + cost
        await self._session.flush()
        return wallet.balance

    async def _get_or_create_settings(self, owner_id: int) -> UserSettings:
        result = await self._session.execute(
            select(UserSettings).where(UserSettings.owner_telegram_id == owner_id)
        )
        settings = result.scalar_one_or_none()
        if settings is None:
            settings = UserSettings(owner_telegram_id=owner_id)
            self._session.add(settings)
            await self._session.flush()
        return settings

    # ── Public read ───────────────────────────────────────────────────────────

    async def get_active_boosts(self, owner_id: int) -> list[dict]:
        now = dt.datetime.now(dt.timezone.utc)
        result = await self._session.execute(
            select(UserBoost)
            .where(
                UserBoost.owner_telegram_id == owner_id,
                UserBoost.expires_at > now,
            )
        )
        boosts = result.scalars().all()
        return [
            {
                "boost_type": b.boost_type,
                "expires_at": b.expires_at.isoformat(),
                "hours_left": max(0, round((b.expires_at - now).total_seconds() / 3600, 1)),
            }
            for b in boosts
        ]

    async def get_settings(self, owner_id: int) -> dict:
        settings = await self._get_or_create_settings(owner_id)
        owned = list(settings.owned_themes or [])
        # "default" is always owned
        if "default" not in owned:
            owned.insert(0, "default")
        return {
            "theme":        settings.theme,
            "frame":        settings.frame,
            "pinned_chat_id": settings.pinned_chat_id,
            "owned_themes": owned,
        }

    async def get_shop_status(self, owner_id: int) -> dict:
        cfg = await self._get_shop_cfg()
        boosts   = await self.get_active_boosts(owner_id)
        settings = await self.get_settings(owner_id)

        def _p(key: str, field: str, default: int) -> int:
            return int(cfg.get(key, {}).get(field, default))

        theme_prices_raw = cfg.get("theme", {}).get("theme_prices", {})
        default_theme_cost = _p("theme", "cost", THEME_COST)
        theme_prices = {
            t: int(theme_prices_raw.get(t, default_theme_cost))
            for t in cfg.get("theme", {}).get("options", list(VALID_THEMES))
        }
        # Always free for default
        theme_prices["default"] = 0

        return {
            "active_boosts": boosts,
            "settings": settings,
            "prices": {
                "double_xp":   _p("double_xp", "cost",   BOOST_DOUBLE_XP_COST),
                "pin_chat":    _p("pin_chat",  "cost",   PIN_CHAT_COST),
                "theme":       default_theme_cost,
                "theme_prices": theme_prices,
                "frame":       _p("frame",     "cost",   FRAME_COST),
                "gift":       _p("gift",      "cost",   GIFT_COST),
                "gift_amount":_p("gift",      "amount", GIFT_AMOUNT),
            },
            "available": {
                "themes": cfg.get("theme", {}).get("options", list(VALID_THEMES)),
                "frames": cfg.get("frame", {}).get("options", list(VALID_FRAMES)),
            },
        }

    # ── Purchases ─────────────────────────────────────────────────────────────

    async def buy_double_xp(self, owner_id: int) -> dict:
        """Buy a double-XP boost (duration from DB config). Extends existing expiry."""
        cfg   = await self._get_shop_cfg()
        cost  = int(cfg.get("double_xp", {}).get("cost",  BOOST_DOUBLE_XP_COST))
        hours = int(cfg.get("double_xp", {}).get("hours", BOOST_DOUBLE_XP_HOURS))

        if not cfg.get("double_xp", {}).get("enabled", True):
            raise ValueError("item_disabled")

        new_balance = await self._deduct(owner_id, cost)
        now = dt.datetime.now(dt.timezone.utc)
        result = await self._session.execute(
            select(UserBoost)
            .where(
                UserBoost.owner_telegram_id == owner_id,
                UserBoost.boost_type == "double_xp",
                UserBoost.expires_at > now,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.expires_at = existing.expires_at + dt.timedelta(hours=hours)
            expires_at = existing.expires_at
        else:
            expires_at = now + dt.timedelta(hours=hours)
            boost = UserBoost(
                owner_telegram_id=owner_id,
                boost_type="double_xp",
                purchased_at=now,
                expires_at=expires_at,
            )
            self._session.add(boost)

        await self._session.flush()
        return {"new_balance": new_balance, "expires_at": expires_at.isoformat()}

    async def buy_theme(self, owner_id: int, theme: str) -> dict:
        """Purchase a new theme (charges coins) or activate a free/owned one."""
        cfg   = await self._get_shop_cfg()
        valid = set(cfg.get("theme", {}).get("options", list(VALID_THEMES)))

        if not cfg.get("theme", {}).get("enabled", True):
            raise ValueError("item_disabled")
        if theme not in valid:
            raise ValueError("invalid_theme")

        settings = await self._get_or_create_settings(owner_id)
        owned = list(settings.owned_themes or [])
        if "default" not in owned:
            owned.insert(0, "default")

        # Already owned — just activate, no charge
        if theme in owned or theme in FREE_THEMES:
            settings.theme = theme
            settings.owned_themes = owned
            await self._session.flush()
            return {"new_balance": None, "theme": theme, "already_owned": True}

        # Per-theme price override, then category cost, then module default
        theme_prices = cfg.get("theme", {}).get("theme_prices", {})
        cost = int(theme_prices.get(theme, cfg.get("theme", {}).get("cost", THEME_COST)))

        new_balance = await self._deduct(owner_id, cost)
        owned.append(theme)
        settings.owned_themes = owned
        settings.theme = theme
        await self._session.flush()
        return {"new_balance": new_balance, "theme": theme, "already_owned": False}

    async def activate_theme(self, owner_id: int, theme: str) -> dict:
        """Activate an already-owned theme — free, no coin deduction."""
        cfg   = await self._get_shop_cfg()
        valid = set(cfg.get("theme", {}).get("options", list(VALID_THEMES)))

        if theme not in valid:
            raise ValueError("invalid_theme")

        settings = await self._get_or_create_settings(owner_id)
        owned = list(settings.owned_themes or [])
        if "default" not in owned:
            owned.insert(0, "default")

        if theme not in owned and theme not in FREE_THEMES:
            raise ValueError("not_owned")

        settings.theme = theme
        settings.owned_themes = owned
        await self._session.flush()
        return {"theme": theme}

    async def buy_frame(self, owner_id: int, frame: str) -> dict:
        cfg  = await self._get_shop_cfg()
        cost = int(cfg.get("frame", {}).get("cost", FRAME_COST))
        valid = set(cfg.get("frame", {}).get("options", list(VALID_FRAMES)))

        if not cfg.get("frame", {}).get("enabled", True):
            raise ValueError("item_disabled")
        if frame not in valid:
            raise ValueError("invalid_frame")

        new_balance = await self._deduct(owner_id, cost)
        settings = await self._get_or_create_settings(owner_id)
        settings.frame = frame
        await self._session.flush()
        return {"new_balance": new_balance, "frame": frame}

    async def pin_chat(self, owner_id: int, chat_id: int | None) -> dict:
        cfg  = await self._get_shop_cfg()
        cost = int(cfg.get("pin_chat", {}).get("cost", PIN_CHAT_COST))

        settings = await self._get_or_create_settings(owner_id)
        if settings.pinned_chat_id == chat_id:
            return {"new_balance": None, "pinned_chat_id": chat_id}

        new_balance: int | None = None
        if chat_id is not None:
            if not cfg.get("pin_chat", {}).get("enabled", True):
                raise ValueError("item_disabled")
            new_balance = await self._deduct(owner_id, cost)

        settings.pinned_chat_id = chat_id
        await self._session.flush()
        return {"new_balance": new_balance, "pinned_chat_id": chat_id}

    async def gift_coins(self, owner_id: int, recipient_id: int) -> dict:
        cfg    = await self._get_shop_cfg()
        cost   = int(cfg.get("gift", {}).get("cost",   GIFT_COST))
        amount = int(cfg.get("gift", {}).get("amount", GIFT_AMOUNT))

        if not cfg.get("gift", {}).get("enabled", True):
            raise ValueError("item_disabled")
        if owner_id == recipient_id:
            raise ValueError("cannot_gift_self")

        new_balance = await self._deduct(owner_id, cost)

        r_result = await self._session.execute(
            select(UserWallet)
            .where(UserWallet.owner_telegram_id == recipient_id)
            .with_for_update()
        )
        r_wallet = r_result.scalar_one_or_none()
        if r_wallet is None:
            r_wallet = UserWallet(
                owner_telegram_id=recipient_id,
                balance=amount,
                total_earned=amount,
                total_spent=0,
            )
            self._session.add(r_wallet)
        else:
            r_wallet.balance += amount
            r_wallet.total_earned = (r_wallet.total_earned or 0) + amount

        await self._session.flush()
        return {"new_balance": new_balance, "gifted_amount": amount}

    # ── Stars coin purchase ───────────────────────────────────────────────────

    async def add_coins_from_purchase(self, owner_id: int, coins: int) -> int:
        """Credit coins to wallet after a successful Stars payment. Returns new balance."""
        result = await self._session.execute(
            select(UserWallet)
            .where(UserWallet.owner_telegram_id == owner_id)
            .with_for_update()
        )
        wallet = result.scalar_one_or_none()
        if wallet is None:
            wallet = UserWallet(
                owner_telegram_id=owner_id,
                balance=coins,
                total_earned=coins,
                total_spent=0,
            )
            self._session.add(wallet)
        else:
            wallet.balance = min(999_999, wallet.balance + coins)
            wallet.total_earned = (wallet.total_earned or 0) + coins
        await self._session.flush()
        return wallet.balance

    # ── Double-XP check (used by pet_repository) ──────────────────────────────

    async def has_double_xp(self, owner_id: int) -> bool:
        now = dt.datetime.now(dt.timezone.utc)
        result = await self._session.execute(
            select(UserBoost.id)
            .where(
                UserBoost.owner_telegram_id == owner_id,
                UserBoost.boost_type == "double_xp",
                UserBoost.expires_at > now,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None
