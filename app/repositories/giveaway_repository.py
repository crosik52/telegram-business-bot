"""GiveawayRepository — giveaway config + top-referrers leaderboard."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business_connection import BusinessConnection
from app.models.giveaway import GiveawayConfig
from app.models.referral import Referral


class GiveawayRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Config ────────────────────────────────────────────────────────────────

    async def get_config(self) -> GiveawayConfig:
        cfg = (
            await self._session.execute(select(GiveawayConfig).limit(1))
        ).scalar_one_or_none()
        if cfg is None:
            cfg = GiveawayConfig()
            self._session.add(cfg)
            await self._session.flush()
        return cfg

    async def update_config(self, **kwargs: object) -> GiveawayConfig:
        cfg = await self.get_config()
        for k, v in kwargs.items():
            setattr(cfg, k, v)
        cfg.updated_at = dt.datetime.now(dt.timezone.utc)
        await self._session.flush()
        return cfg

    def _cfg_dict(self, cfg: GiveawayConfig) -> dict:
        return {
            "is_active": cfg.is_active,
            "is_visible_to_all": cfg.is_visible_to_all,
            "deadline": cfg.deadline.isoformat() if cfg.deadline else None,
            "prize_1": cfg.prize_1,
            "prize_2": cfg.prize_2,
            "prize_3": cfg.prize_3,
            "description": cfg.description,
            "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
        }

    # ── Leaderboard ───────────────────────────────────────────────────────────

    async def get_top_referrers(self, limit: int = 3) -> list[dict]:
        """Return top referrers by active referral count, with display names."""
        rows = (
            await self._session.execute(
                select(
                    Referral.referrer_telegram_id,
                    func.count(Referral.id).label("cnt"),
                )
                .where(Referral.status == "active")
                .group_by(Referral.referrer_telegram_id)
                .order_by(func.count(Referral.id).desc())
                .limit(limit)
            )
        ).all()

        if not rows:
            return []

        referrer_ids = [r[0] for r in rows]

        # Best name per user_telegram_id from BusinessConnection
        name_rows = (
            await self._session.execute(
                select(
                    BusinessConnection.user_telegram_id,
                    BusinessConnection.user_first_name,
                    BusinessConnection.user_last_name,
                    BusinessConnection.user_username,
                )
                .where(BusinessConnection.user_telegram_id.in_(referrer_ids))
                .distinct(BusinessConnection.user_telegram_id)
            )
        ).all()

        name_map: dict[int, dict] = {}
        for uid, fn, ln, un in name_rows:
            display = " ".join(p for p in [fn, ln] if p).strip()
            if not display:
                display = f"Пользователь {uid}"
            name_map[uid] = {"display": display, "username": un}

        result = []
        for rank, (uid, cnt) in enumerate(rows, start=1):
            info = name_map.get(uid, {})
            result.append(
                {
                    "rank": rank,
                    "telegram_id": uid,
                    "display_name": info.get("display", f"Пользователь {uid}"),
                    "username": info.get("username"),
                    "referral_count": int(cnt),
                }
            )
        return result
