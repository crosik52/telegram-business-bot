"""SubscriptionRepository — subscription config + per-user activation."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import DEFAULT_BENEFITS, SubscriptionConfig, UserSubscription


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Config (singleton) ────────────────────────────────────────────────────

    async def get_config(self) -> SubscriptionConfig:
        result = await self._session.execute(select(SubscriptionConfig).limit(1))
        config = result.scalar_one_or_none()
        if config is None:
            config = SubscriptionConfig(benefits=dict(DEFAULT_BENEFITS))
            self._session.add(config)
            await self._session.flush()
        return config

    async def update_config(self, **fields) -> SubscriptionConfig:
        config = await self.get_config()
        allowed = {"is_enabled", "price_stars", "duration_days", "title", "description", "benefits"}
        for key, value in fields.items():
            if key in allowed:
                setattr(config, key, value)
        config.updated_at = dt.datetime.now(dt.timezone.utc)
        await self._session.flush()
        return config

    # ── User subscriptions ────────────────────────────────────────────────────

    async def get_active_subscription(self, user_telegram_id: int) -> UserSubscription | None:
        now = dt.datetime.now(dt.timezone.utc)
        result = await self._session.execute(
            select(UserSubscription)
            .where(
                UserSubscription.user_telegram_id == user_telegram_id,
                UserSubscription.is_active.is_(True),
                UserSubscription.expires_at > now,
            )
            .order_by(UserSubscription.expires_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def activate(
        self,
        user_telegram_id: int,
        charge_id: str,
        stars_paid: int,
        duration_days: int,
    ) -> UserSubscription:
        """Activate a Stars-purchased subscription (deactivates any existing one).

        Idempotent: if *charge_id* was already recorded, returns the existing
        subscription without creating a duplicate (guards against Telegram
        retransmitting successful_payment).
        """
        # Idempotency check — same charge already processed → return it
        existing_charge = await self._session.execute(
            select(UserSubscription).where(
                UserSubscription.payment_charge_id == charge_id
            )
        )
        found = existing_charge.scalar_one_or_none()
        if found is not None:
            return found

        await self._deactivate_user_subs(user_telegram_id)
        now = dt.datetime.now(dt.timezone.utc)
        sub = UserSubscription(
            user_telegram_id=user_telegram_id,
            is_active=True,
            started_at=now,
            expires_at=now + dt.timedelta(days=duration_days),
            granted_by_admin=False,
            payment_charge_id=charge_id,
            stars_paid=stars_paid,
        )
        self._session.add(sub)
        await self._session.flush()
        return sub

    async def grant(self, user_telegram_id: int, duration_days: int) -> UserSubscription:
        """Admin: grant subscription without payment."""
        await self._deactivate_user_subs(user_telegram_id)
        now = dt.datetime.now(dt.timezone.utc)
        sub = UserSubscription(
            user_telegram_id=user_telegram_id,
            is_active=True,
            started_at=now,
            expires_at=now + dt.timedelta(days=duration_days),
            granted_by_admin=True,
            stars_paid=0,
        )
        self._session.add(sub)
        await self._session.flush()
        return sub

    async def revoke(self, user_telegram_id: int) -> None:
        """Admin: deactivate any active subscription."""
        await self._deactivate_user_subs(user_telegram_id)

    async def _deactivate_user_subs(self, user_telegram_id: int) -> None:
        await self._session.execute(
            update(UserSubscription)
            .where(
                UserSubscription.user_telegram_id == user_telegram_id,
                UserSubscription.is_active.is_(True),
            )
            .values(is_active=False)
        )

    async def list_subscribers(self, page: int = 1, page_size: int = 30) -> dict:
        now = dt.datetime.now(dt.timezone.utc)
        count_q = await self._session.execute(
            select(func.count())
            .select_from(UserSubscription)
            .where(
                UserSubscription.is_active.is_(True),
                UserSubscription.expires_at > now,
            )
        )
        total = count_q.scalar_one()
        result = await self._session.execute(
            select(UserSubscription)
            .where(
                UserSubscription.is_active.is_(True),
                UserSubscription.expires_at > now,
            )
            .order_by(UserSubscription.expires_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        subs = result.scalars().all()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "subscribers": [
                {
                    "user_telegram_id": s.user_telegram_id,
                    "started_at": s.started_at.isoformat(),
                    "expires_at": s.expires_at.isoformat(),
                    "days_left": max(0, (s.expires_at - now).days),
                    "granted_by_admin": s.granted_by_admin,
                    "stars_paid": s.stars_paid,
                }
                for s in subs
            ],
        }
