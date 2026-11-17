"""ShopRepository — coin-spending actions beyond pets and casino."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json

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

# Compatibility exports for existing route imports; these products are retired.
VALID_THEMES: set[str] = set()
VALID_FRAMES: set[str] = set()


def _active_shop_config(items: dict) -> dict:
    return {
        k: copy.deepcopy(v) for k, v in items.items()
        if k not in ("theme", "frame") and not k.startswith("_")
    }


class ShopRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Shop config (singleton) ───────────────────────────────────────────────

    async def _get_shop_cfg(self) -> dict:
        """Read the active catalog without changing historical configuration."""
        result = await self._session.execute(select(ShopConfig).where(ShopConfig.id == 1))
        cfg_row = result.scalar_one_or_none()
        stored = cfg_row.items if cfg_row else DEFAULT_SHOP_CONFIG
        return _active_shop_config(stored or DEFAULT_SHOP_CONFIG)

    async def get_shop_config_admin(self) -> dict:
        """Admin: return full shop config dict."""
        result = await self._session.execute(select(ShopConfig).where(ShopConfig.id == 1))
        cfg = result.scalar_one_or_none()
        if cfg is None:
            # Create the singleton row with defaults
            cfg = ShopConfig(id=1, items=copy.deepcopy(DEFAULT_SHOP_CONFIG))
            self._session.add(cfg)
            await self._session.flush()
        return _active_shop_config(cfg.items or DEFAULT_SHOP_CONFIG)

    async def update_shop_config(self, items: dict) -> dict:
        """Update active products while retaining archived customization data."""
        result = await self._session.execute(
            select(ShopConfig).where(ShopConfig.id == 1).with_for_update()
        )
        cfg = result.scalar_one_or_none()
        active = _active_shop_config(items)
        if cfg is None:
            cfg = ShopConfig(id=1, items=active)
            self._session.add(cfg)
        else:
            archived = {
                k: copy.deepcopy(v) for k, v in (cfg.items or {}).items()
                if k in ("theme", "frame") or k.startswith("_")
            }
            cfg.items = {**active, **archived}
        await self._session.flush()
        return _active_shop_config(cfg.items)

    async def get_navigation(self) -> dict[str, bool]:
        """Read navigation without creating a row or changing the shop catalog."""
        result = await self._session.execute(select(ShopConfig).where(ShopConfig.id == 1))
        cfg = result.scalar_one_or_none()
        navigation = (cfg.items or {}).get("_navigation", {}) if cfg else {}
        return {"giveaway_tab_visible": navigation.get("giveaway_tab_visible") is True}

    async def set_giveaway_tab_visible(self, visible: bool) -> dict[str, bool]:
        # Serialize with catalog writes so neither setting can overwrite the other.
        result = await self._session.execute(
            select(ShopConfig).where(ShopConfig.id == 1).with_for_update()
        )
        cfg = result.scalar_one_or_none()
        if cfg is None:
            cfg = ShopConfig(id=1, items=copy.deepcopy(DEFAULT_SHOP_CONFIG))
            self._session.add(cfg)
        items = copy.deepcopy(cfg.items or DEFAULT_SHOP_CONFIG)
        items["_navigation"] = {**items.get("_navigation", {}), "giveaway_tab_visible": visible}
        cfg.items = items
        await self._session.flush()
        return {"giveaway_tab_visible": visible}

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
        from app.repositories.chat_list_repository import preferences
        settings = await self._get_or_create_settings(owner_id)
        prefs = preferences(settings)
        return {"pinned_chat_id": prefs.pinned[0] if prefs.pinned else None}

    async def get_prices_version(self) -> str:
        """Return a short hash of the price-relevant shop config.
        Changes whenever an admin updates prices, so clients can detect staleness cheaply."""
        cfg = await self._get_shop_cfg()
        price_keys = ("double_xp", "pin_chat", "gift")
        price_slice = {k: cfg.get(k) for k in price_keys}
        raw = json.dumps(price_slice, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:12]

    async def get_shop_status(self, owner_id: int) -> dict:
        cfg = await self._get_shop_cfg()
        boosts   = await self.get_active_boosts(owner_id)
        settings = await self.get_settings(owner_id)

        def _p(key: str, field: str, default: int) -> int:
            return int(cfg.get(key, {}).get(field, default))

        # Version hash so clients can detect price changes without a full re-fetch.
        price_keys = ("double_xp", "pin_chat", "gift")
        price_slice = {k: cfg.get(k) for k in price_keys}
        prices_version = hashlib.sha256(
            json.dumps(price_slice, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]

        return {
            "active_boosts": boosts,
            "settings": settings,
            "prices": {
                "double_xp":   _p("double_xp", "cost",   BOOST_DOUBLE_XP_COST),
                "pin_chat":    0,
                "gift":       _p("gift",      "cost",   GIFT_COST),
                "gift_amount":_p("gift",      "amount", GIFT_AMOUNT),
            },
            "prices_version": prices_version,
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
        raise ValueError("item_disabled")

    async def activate_theme(self, owner_id: int, theme: str) -> dict:
        raise ValueError("item_disabled")

    async def buy_frame(self, owner_id: int, frame: str) -> dict:
        raise ValueError("item_disabled")

    async def pin_chat(self, owner_id: int, chat_id: int | None) -> dict:
        from app.repositories.chat_list_repository import ChatListRepository
        prefs = await ChatListRepository(self._session).change(
            owner_id, "pin" if chat_id is not None else "unpin_all", chat_id)
        balance = await self._session.scalar(select(UserWallet.balance)
            .where(UserWallet.owner_telegram_id == owner_id))
        return {"new_balance": balance or 0, "pinned_chat_id": prefs.pinned[0] if prefs.pinned else None,
                "pinned_chat_ids": list(prefs.pinned)}

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
