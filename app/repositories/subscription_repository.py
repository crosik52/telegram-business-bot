"""SubscriptionRepository — subscription config + per-user activation."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, outerjoin, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.subscription import (
    DEFAULT_BENEFITS, DEFAULT_VIP_BENEFITS,
    SubscriptionConfig, VipSubscriptionConfig, UserSubscription,
)
from app.models.user import TelegramUser


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Config (singleton) ────────────────────────────────────────────────────

    async def get_config(self) -> SubscriptionConfig:
        result = await self._session.execute(select(SubscriptionConfig).limit(1))
        config = result.scalar_one_or_none()
        if config is None:
            config = SubscriptionConfig(is_enabled=True, benefits=dict(DEFAULT_BENEFITS))
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

    # ── VIP Config (singleton) ────────────────────────────────────────────────

    async def get_vip_config(self) -> VipSubscriptionConfig:
        result = await self._session.execute(select(VipSubscriptionConfig).limit(1))
        config = result.scalar_one_or_none()
        if config is None:
            config = VipSubscriptionConfig(is_enabled=True, benefits=dict(DEFAULT_VIP_BENEFITS))
            self._session.add(config)
            await self._session.flush()
        return config

    async def update_vip_config(self, **fields) -> VipSubscriptionConfig:
        config = await self.get_vip_config()
        allowed = {"is_enabled", "price_stars", "duration_days", "title", "description", "benefits"}
        for key, value in fields.items():
            if key in allowed:
                setattr(config, key, value)
        config.updated_at = dt.datetime.now(dt.timezone.utc)
        await self._session.flush()
        return config

    # ── User subscriptions ────────────────────────────────────────────────────

    async def get_active_subscription(self, user_telegram_id: int) -> UserSubscription | None:
        """Return the highest-tier active subscription (VIP preferred over Premium)."""
        now = dt.datetime.now(dt.timezone.utc)
        result = await self._session.execute(
            select(UserSubscription)
            .where(
                UserSubscription.user_telegram_id == user_telegram_id,
                UserSubscription.is_active.is_(True),
                UserSubscription.expires_at > now,
            )
            # VIP rows first (sub_type='vip'), then by expiry descending
            .order_by(UserSubscription.sub_type.desc(), UserSubscription.expires_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_active_vip_subscription(self, user_telegram_id: int) -> UserSubscription | None:
        """Return the active VIP subscription for this user, if any."""
        now = dt.datetime.now(dt.timezone.utc)
        result = await self._session.execute(
            select(UserSubscription)
            .where(
                UserSubscription.user_telegram_id == user_telegram_id,
                UserSubscription.is_active.is_(True),
                UserSubscription.expires_at > now,
                UserSubscription.sub_type == "vip",
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
        sub_type: str = "premium",
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
            status="active",
            sub_type=sub_type,
            started_at=now,
            expires_at=now + dt.timedelta(days=duration_days),
            granted_by_admin=False,
            payment_charge_id=charge_id,
            stars_paid=stars_paid,
        )
        self._session.add(sub)
        await self._session.flush()
        return sub

    async def grant(
        self,
        user_telegram_id: int,
        duration_days: int,
        sub_type: str = "premium",
    ) -> UserSubscription:
        """Admin: grant subscription without payment."""
        await self._deactivate_user_subs(user_telegram_id)
        now = dt.datetime.now(dt.timezone.utc)
        sub = UserSubscription(
            user_telegram_id=user_telegram_id,
            is_active=True,
            status="active",
            sub_type=sub_type,
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
        await self._deactivate_user_subs(user_telegram_id, new_status="cancelled")

    async def _deactivate_user_subs(
        self, user_telegram_id: int, new_status: str = "cancelled"
    ) -> None:
        """Set is_active=False and status=new_status on all active rows for this user."""
        await self._session.execute(
            update(UserSubscription)
            .where(
                UserSubscription.user_telegram_id == user_telegram_id,
                UserSubscription.is_active.is_(True),
            )
            .values(is_active=False, status=new_status)
        )

    async def get_stats(self) -> dict:
        """Quick aggregate stats for the admin panel header."""
        now = dt.datetime.now(dt.timezone.utc)
        active_count = (
            await self._session.execute(
                select(func.count())
                .select_from(UserSubscription)
                .where(UserSubscription.is_active.is_(True), UserSubscription.expires_at > now)
            )
        ).scalar_one()
        total_count = (
            await self._session.execute(select(func.count()).select_from(UserSubscription))
        ).scalar_one()
        total_stars = (
            await self._session.execute(
                select(func.coalesce(func.sum(UserSubscription.stars_paid), 0))
                .select_from(UserSubscription)
            )
        ).scalar_one()
        return {
            "active": active_count,
            "total": total_count,
            "total_stars": int(total_stars),
        }

    async def list_subscribers(
        self,
        page: int = 1,
        page_size: int = 25,
        status_filter: str | None = None,
    ) -> dict:
        """List subscription rows for the admin panel with user identity info.

        JOINs TelegramUser so the admin sees first_name, last_name, username
        alongside the Telegram ID.  Falls back gracefully when the user has no
        TelegramUser row (outer join).

        Ordering: active rows first (soonest-to-expire at the top), then all
        inactive rows by expires_at desc so the most-recent history is visible.
        """
        now = dt.datetime.now(dt.timezone.utc)

        base_where = []
        if status_filter:
            base_where.append(UserSubscription.status == status_filter)

        count_q = await self._session.execute(
            select(func.count())
            .select_from(UserSubscription)
            .where(*base_where)
        )
        total = count_q.scalar_one()

        # LEFT OUTER JOIN so subscriptions without a TelegramUser row are not dropped
        j = outerjoin(
            UserSubscription,
            TelegramUser,
            UserSubscription.user_telegram_id == TelegramUser.telegram_user_id,
        )
        result = await self._session.execute(
            select(UserSubscription, TelegramUser)
            .select_from(j)
            .where(*base_where)
            .order_by(
                UserSubscription.is_active.desc(),
                UserSubscription.expires_at.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = result.all()

        subscribers = []
        for sub, user in rows:
            first_name = getattr(user, "first_name", None) or ""
            last_name  = getattr(user, "last_name",  None) or ""
            username   = getattr(user, "username",   None) or ""
            full_name  = " ".join(p for p in (first_name, last_name) if p) or None
            subscribers.append({
                "user_telegram_id": sub.user_telegram_id,
                "first_name":       first_name,
                "last_name":        last_name,
                "username":         username,
                "full_name":        full_name or username or str(sub.user_telegram_id),
                "status":           sub.status,
                "started_at":       sub.started_at.isoformat(),
                "expires_at":       sub.expires_at.isoformat(),
                "days_left":        max(0, (sub.expires_at - now).days),
                "granted_by_admin": sub.granted_by_admin,
                "stars_paid":       sub.stars_paid,
                "sub_type":         sub.sub_type or "premium",
            })

        return {
            "total":       total,
            "page":        page,
            "page_size":   page_size,
            "subscribers": subscribers,
        }
