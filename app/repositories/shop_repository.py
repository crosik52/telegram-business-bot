"""ShopRepository — coin-spending actions beyond pets and casino."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.boost import UserBoost
from app.models.user_settings import UserSettings
from app.models.wallet import UserWallet

# ── Prices (coins) ────────────────────────────────────────────────────────────
BOOST_DOUBLE_XP_COST = 200
BOOST_DOUBLE_XP_HOURS = 24

PIN_CHAT_COST = 75
THEME_COST = 100
FRAME_COST = 150
GIFT_COST = 30   # coins deducted from sender; recipient gets GIFT_AMOUNT
GIFT_AMOUNT = 50  # coins credited to recipient

VALID_THEMES = {"default", "dark_forest", "ocean", "sunset", "lavender"}
VALID_FRAMES = {"none", "stars", "flowers", "fire", "neon"}


class ShopRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        return {
            "theme": settings.theme,
            "frame": settings.frame,
            "pinned_chat_id": settings.pinned_chat_id,
        }

    async def get_shop_status(self, owner_id: int) -> dict:
        boosts = await self.get_active_boosts(owner_id)
        settings = await self.get_settings(owner_id)
        return {
            "active_boosts": boosts,
            "settings": settings,
            "prices": {
                "double_xp": BOOST_DOUBLE_XP_COST,
                "pin_chat": PIN_CHAT_COST,
                "theme": THEME_COST,
                "frame": FRAME_COST,
                "gift": GIFT_COST,
                "gift_amount": GIFT_AMOUNT,
            },
        }

    # ── Purchases ─────────────────────────────────────────────────────────────

    async def buy_double_xp(self, owner_id: int) -> dict:
        """Buy a 24h double-XP boost. Stacks: extends existing expiry by 24h."""
        new_balance = await self._deduct(owner_id, BOOST_DOUBLE_XP_COST)

        now = dt.datetime.now(dt.timezone.utc)
        # Check for an existing active double_xp boost to extend
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
            existing.expires_at = existing.expires_at + dt.timedelta(hours=BOOST_DOUBLE_XP_HOURS)
            expires_at = existing.expires_at
        else:
            expires_at = now + dt.timedelta(hours=BOOST_DOUBLE_XP_HOURS)
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
        if theme not in VALID_THEMES:
            raise ValueError("invalid_theme")
        new_balance = await self._deduct(owner_id, THEME_COST)
        settings = await self._get_or_create_settings(owner_id)
        settings.theme = theme
        await self._session.flush()
        return {"new_balance": new_balance, "theme": theme}

    async def buy_frame(self, owner_id: int, frame: str) -> dict:
        if frame not in VALID_FRAMES:
            raise ValueError("invalid_frame")
        new_balance = await self._deduct(owner_id, FRAME_COST)
        settings = await self._get_or_create_settings(owner_id)
        settings.frame = frame
        await self._session.flush()
        return {"new_balance": new_balance, "frame": frame}

    async def pin_chat(self, owner_id: int, chat_id: int | None) -> dict:
        """Pin or unpin a chat. Costs PIN_CHAT_COST if pinning for the first time / changing."""
        settings = await self._get_or_create_settings(owner_id)
        if settings.pinned_chat_id == chat_id:
            # No change — free
            return {"new_balance": None, "pinned_chat_id": chat_id}

        new_balance: int | None = None
        if chat_id is not None:  # pinning (not unpinning) costs coins
            new_balance = await self._deduct(owner_id, PIN_CHAT_COST)

        settings.pinned_chat_id = chat_id
        await self._session.flush()
        return {"new_balance": new_balance, "pinned_chat_id": chat_id}

    async def gift_coins(self, owner_id: int, recipient_id: int) -> dict:
        """Deduct GIFT_COST from sender, credit GIFT_AMOUNT to recipient."""
        if owner_id == recipient_id:
            raise ValueError("cannot_gift_self")

        new_balance = await self._deduct(owner_id, GIFT_COST)

        # Credit recipient (create wallet if needed)
        r_result = await self._session.execute(
            select(UserWallet)
            .where(UserWallet.owner_telegram_id == recipient_id)
            .with_for_update()
        )
        r_wallet = r_result.scalar_one_or_none()
        if r_wallet is None:
            r_wallet = UserWallet(
                owner_telegram_id=recipient_id,
                balance=GIFT_AMOUNT,
                total_earned=GIFT_AMOUNT,
                total_spent=0,
            )
            self._session.add(r_wallet)
        else:
            r_wallet.balance += GIFT_AMOUNT
            r_wallet.total_earned = (r_wallet.total_earned or 0) + GIFT_AMOUNT

        await self._session.flush()
        return {"new_balance": new_balance, "gifted_amount": GIFT_AMOUNT}

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
